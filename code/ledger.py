"""Evidence-aware reconstruction and conservative monthly recurrence inference."""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from domain import DataError, Flow, Forecast, ZERO, choices, day, money

VARIABLE = {'groceries', 'transport', 'dining', 'shopping', 'entertainment'}
IRREGULAR = re.compile(r'bonus|commission|arrears|prorat|seasonal|temporary assignment|gig |freelanc|invoice|lottery|prize|refund|reversal|transfer|final employer|final payroll|severance|platform payout|marketplace payout|app earnings', re.I)


def month_add(d, n, wanted_day=None):
    month = d.year * 12 + d.month - 1 + n
    year, m = divmod(month, 12)
    m += 1
    return date(year, m, min(wanted_day or d.day, calendar.monthrange(year, m)[1]))


def monthly_dates(start, end, anchor):
    d = date(start.year, start.month, min(anchor, calendar.monthrange(start.year,start.month)[1]))
    while d <= end:
        if d >= start:
            yield d
        d = month_add(d, 1, anchor)


def reconstruct(dataset, request, evidence):
    start = day(request['request_date'])
    end = start + timedelta(days=90)
    p = dataset.profiles[request['user_id']]
    home = p['home_currency']
    events = [dict(e) for e in dataset.by_user['financial_events'][request['user_id']]]
    by_id = {e['event_id']:e for e in events}
    notes = []
    category_facts = []
    for fact in evidence.facts:
        if not fact.event_id:
            category_facts.append(fact)
            continue
        e = by_id[fact.event_id]
        if fact.action in ('cancel','exclude'):
            e['status'] = 'cancelled'
        else:
            if fact.amount is not None:
                e['amount'] = fact.amount
            if fact.currency:
                e['currency'] = fact.currency
            if fact.effective_date:
                e['settlement_date'] = fact.effective_date
            if fact.status:
                e['status'] = fact.status
            if fact.direction and fact.direction != e['direction']:
                raise DataError('Evidence changed transaction direction; explicit reconciliation required')
        notes.append(f'{",".join(fact.source_ids)}: {fact.note}')
    for e in events:
        if not e['amount']:
            raise DataError(f'Unresolved amount for {e["event_id"]}')

    # A settled successor replaces its pending/scheduled authorization, not a
    # different cash leg such as an investment purchase followed by a sale.
    superseded = set()
    for e in events:
        old = by_id.get(e['linked_event_id'])
        if (old and old['status'] in ('pending','scheduled') and
            e['status'] in ('settled','cancelled') and e['direction'] == old['direction']
            and e['category'] == old['category']):
            superseded.add(old['event_id'])
    history = []
    flows = []
    seen = set()
    for e in events:
        if e['event_id'] in superseded or e['status'] in ('failed','cancelled','unrealized'):
            continue
        when = day(e['settlement_date'] or e['event_date'])
        amount = dataset.convert(e['amount'],e['currency'],home,when)
        fingerprint = tuple(e[k] for k in ('event_type','description','category','direction','amount','currency','settlement_date','status'))
        # Only exact duplicate representations, never just matching amounts.
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        e['_date'], e['_amount'] = when, amount
        if e['status'] == 'settled' and when <= start:
            history.append(e)
            continue
        if e['direction'] == 'credit':
            if e['status'] == 'pending':
                continue
            if e['status'] != 'settled' and (e['category'] != 'salary' or IRREGULAR.search(e['description'])):
                continue
        # Reserve pending debits now, including an authorization with a later date.
        due = start if e['status'] == 'pending' else max(start, when)
        if due <= end:
            sign = 1 if e['direction']=='credit' else -1
            flows.append(Flow(due, sign*amount,e['event_id'],e['category']))

    monthly = defaultdict(list)
    for e in history:
        if e['_date'] < month_add(start,-6) or e['direction'] != 'debit':
            continue
        if e['event_type'] in ('investment_valuation','investment_sale'):
            continue
        # Variable essentials are budgeted by category; fixed bills by description.
        key = (e['category'], '' if e['category'] in VARIABLE else e['description'].strip().casefold())
        monthly[key].append(e)
    adjustable = {}
    for (category, _), rows in monthly.items():
        buckets = defaultdict(list)
        for e in rows:
            if (e['_date'].year,e['_date'].month) < (start.year,start.month):
                buckets[(e['_date'].year,e['_date'].month)].append(e)
        if len(buckets) < 2:
            continue
        recent = sorted(buckets)[-3:]
        last = max(rows,key=lambda e:e['_date'])
        if (start-last['_date']).days > 62:
            continue
        totals = [sum((e['_amount'] for e in buckets[m]),ZERO) for m in recent]
        amount = max(totals)
        latest_rows = buckets[recent[-1]]
        variable = any(len(buckets[m]) > 1 for m in recent)
        source = last['event_id']
        if variable:
            # Reserve a conservative monthly budget in daily portions; ensures
            # changing merchant descriptions do not create fictional subscriptions.
            d = start
            while d <= end:
                per_day = amount / calendar.monthrange(d.year,d.month)[1]
                flows.append(Flow(d,-per_day,source,category,True))
                d += timedelta(days=1)
        else:
            anchor = int(median(e['_date'].day for e in rows))
            for due in monthly_dates(start,end,anchor):
                # Settled at the opening date is already in the opening balance.
                if any(e['_date']==due for e in rows):
                    continue
                if any(f.category==category and f.when==due and not f.recurring for f in flows):
                    continue
                flows.append(Flow(due,-amount,source,category,True))
        if not variable and last['flexibility'] in ('reducible','stoppable','reducible_or_stoppable'):
            adjustable[source] = dict(last, forecast_amount=str(amount), home_currency=home)

    salaries = [e for e in history if e['direction']=='credit' and e['category']=='salary']
    salary_groups=defaultdict(list)
    employment_ended=any(re.search(r'final employer|final payroll|severance',e['description'],re.I)
                         and (start-e['_date']).days<=62 for e in salaries)
    for e in salaries:
        if not IRREGULAR.search(e['description']):
            salary_groups[e['description'].casefold()].append(e)
    # A confirmed regular payroll plus earlier salary history supports continuity
    # after a prorated first paycheck. Do not extrapolate a one-off award/invoice.
    scheduled=[e for e in events if e.get('_date') and e['status']=='scheduled'
               and e['category']=='salary' and not IRREGULAR.search(e['description'])]
    if not salary_groups and salaries and scheduled and not employment_ended:
        salary_groups['confirmed regular salary']=scheduled
    for group in salary_groups.values():
        if employment_ended: continue
        months={(e['_date'].year,e['_date'].month) for e in group}
        if len(months)<2 and group!=scheduled: continue
        recent_salary=sorted(group,key=lambda e:e['_date'])[-3:]
        last=recent_salary[-1]
        if (start-last['_date']).days>40: continue
        native_amount=min(money(e['amount']) for e in recent_salary)
        anchor=int(median(e['_date'].day for e in recent_salary))
        for due in monthly_dates(start,end,anchor):
            if any(e['_date']==due for e in group) and due<=start: continue
            if any(f.category=='salary' and f.when==due for f in flows): continue
            amount=dataset.convert(native_amount,last['currency'],home,due)
            flows.append(Flow(due,amount,last['event_id'],'salary',True))

    for fact in category_facts:
        category = fact.category
        effective = day(fact.effective_date) if fact.effective_date else start
        affected = sorted([f for f in flows if f.category==category and f.when>=start],key=lambda f:f.when)
        if fact.action in ('cancel','exclude'):
            targets = [f for f in affected if f.when>=effective]
            if not fact.recurring:
                targets = targets[:1]
            flows = [f for f in flows if f not in targets]
        elif fact.action in ('amend','confirm','add'):
            if fact.action=='add' or not affected:
                if fact.amount is None or not fact.effective_date or not fact.direction:
                    raise DataError(f'New {category} commitment lacks amount/date/direction')
                dates = monthly_dates(max(start,effective),end,effective.day) if fact.recurring else [effective]
                for due in dates:
                    if start <= due <= end:
                        amount = dataset.convert(fact.amount,fact.currency or home,home,due)
                        flows.append(Flow(due,amount if fact.direction=='credit' else -amount,
                                          fact.source_ids[0],category,fact.recurring))
            else:
                targets = [f for f in affected if f.when>=effective] if fact.recurring else affected[:1]
                for old in targets:
                    due = old.when if fact.recurring or not fact.effective_date else max(start,effective)
                    amount = abs(old.amount)
                    if fact.amount is not None:
                        amount = dataset.convert(fact.amount,fact.currency or home,home,due)
                    elif fact.multiplier is not None:
                        amount *= money(fact.multiplier)
                    flows.remove(old)
                    if due<=end:
                        flows.append(replace(old,when=due,amount=amount if old.amount>=0 else -amount))
        notes.append(f'{",".join(fact.source_ids)}: {fact.note}')
    return Forecast(start,end,money(p['current_available_balance']),money(p['minimum_balance_to_keep']),
                    sorted(flows,key=lambda f:(f.when,f.amount,f.source)),adjustable,notes)
