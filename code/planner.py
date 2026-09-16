"""Enumerate allowed plans; affordability is calculated, never model-generated."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from itertools import combinations, product

from domain import (Adjustment, Candidate, DataError, ZERO, choices, day,
                    floor_money, fmt, money)


def changed_flows(forecast, changes):
    change_map = {c.event_id:c for c in changes}
    for f in forecast.flows:
        amount = f.amount
        c = change_map.get(f.source)
        if c and f.recurring and amount < 0:
            amount = ZERO if c.kind=='stop' else -c.new_amount
        yield f.when, amount, f.source


def balances(forecast, payments=(), changes=()):
    entries = defaultdict(list)
    for when, amount, source in changed_flows(forecast,changes):
        entries[when].append((amount,source))
    paid = defaultdict(list)
    for when, amount in payments:
        paid[when].append(amount)
    balance = forecast.opening
    low = balance
    daily = []
    d = forecast.start
    while d <= forecast.end:
        # Conservative same-day ordering: reserved/essential debits, then posted
        # income, then discretionary request payments. Check intermediate states.
        for amount, _ in sorted(entries[d]):
            balance += amount
            low = min(low,balance)
        for amount in paid[d]:
            balance -= amount
            low = min(low,balance)
        daily.append((d,balance,low))
        d += timedelta(days=1)
    return daily


def feasible(forecast, payments=(), changes=()):
    if any(d<forecast.start or d>forecast.end or a<0 for d,a in payments):
        return False
    return balances(forecast,payments,changes)[-1][2] >= forecast.minimum


def capacity(forecast, requested):
    baseline = balances(forecast)
    if baseline[-1][2] < forecast.minimum:
        return ZERO, None
    # A payment at end of day affects every subsequent balance, including every
    # intermediate debit on later days. Binary search cents using full replay.
    lo, hi = 0, int(requested*100)
    while lo < hi:
        mid=(lo+hi+1)//2
        if feasible(forecast,[(forecast.start,Decimal(mid)/100)]): lo=mid
        else: hi=mid-1
    safe=Decimal(lo)/100
    earliest=next((d for d,_,_ in baseline if feasible(forecast,[(d,requested)])),None)
    return safe,earliest


def adjustment_sets(forecast, profile):
    groups=[]
    protect=choices(profile['expense_categories_to_protect'])
    for eid,e in sorted(forecast.adjustable.items()):
        cat=e['category']
        if cat in protect: continue
        amount=money(e['forecast_amount'])
        opts=[]
        if cat in choices(profile['expense_categories_user_is_willing_to_stop']) and 'stoppable' in e['flexibility']:
            opts.append(Adjustment(eid,'stop',ZERO,amount))
        if cat in choices(profile['expense_categories_user_is_willing_to_reduce']) and 'reducible' in e['flexibility'] and e['minimum_allowed_amount']:
            # Currency conversion of a reduction bound uses the same historical rate.
            minimum=money(e['minimum_allowed_amount'])
            if e['currency'] != e['home_currency']:
                minimum *= e['_amount']/money(e['amount'])
            if minimum < amount:
                opts.append(Adjustment(eid,'reduce_to',minimum,amount))
        if opts: groups.append(opts)
    yield ()
    for n in range(1,min(3,len(groups))+1):
        for subset in combinations(groups,n):
            yield from product(*subset)


def candidates(forecast, request, profile, options, safe, earliest, changes):
    amount=money(request['requested_amount'])
    deadline=min(day(request['desired_completion_date']),forecast.end)
    methods=choices(profile['payment_methods_user_will_consider'])
    if 'full_payment' in methods:
        when=forecast.start
        while when<=deadline:
            if feasible(forecast,[(when,amount)],changes):
                yield Candidate('full_payment' if when==forecast.start else 'wait',((when,amount),),changes)
                break
            when+=timedelta(days=1)
    if ('partial_payment' in methods and request['allows_partial_payment']=='true'
        and ZERO<safe<amount and earliest and forecast.start<earliest<=deadline):
        plan=((forecast.start,safe),(earliest,amount-safe))
        if feasible(forecast,plan,changes):
            yield Candidate('partial_payment',plan,changes)
    if 'installments' in methods and profile['max_installment_months']:
        limit=int(profile['max_installment_months'])
        for o in options:
            if o['payment_method']!='installments': continue
            count=int(o['number_of_payments'])
            interval=int(o['payment_frequency_days'] or '0')
            first=day(o['first_payment_date'])
            plan=tuple((first+timedelta(days=i*interval),money(o['payment_amount'])) for i in range(count))
            # Offers are monthly installments in this dataset: respect both count
            # and calendar span; never manufacture a cheaper final installment.
            if count>limit or not plan or plan[0][0]<forecast.start or plan[-1][0]>deadline: continue
            if (plan[-1][0]-first).days > limit*31: continue
            if feasible(forecast,plan,changes):
                yield Candidate('installments',plan,changes,o['payment_option_id'])


def decide(forecast, request, profile, options):
    amount=money(request['requested_amount'])
    safe,earliest=capacity(forecast,amount)
    valid=[]
    for changes in adjustment_sets(forecast,profile):
        if changes and valid and not valid[0].changes:
            break  # No spending changes is a strict higher-priority preference.
        valid.extend(candidates(forecast,request,profile,options,safe,earliest,changes))
    chosen=min(valid,key=lambda c:c.rank()) if valid else None
    if chosen is None:
        status='affordable_later' if earliest and earliest>forecast.start else 'not_affordable'
        method='not_recommended'
        plan='none'
        changes='none'
        explanation=(f'Safe today: {profile["home_currency"]} {fmt(safe)}. '
                     'No eligible payment plan completes by the deadline while maintaining '
                     f'the {fmt(forecast.minimum)} minimum over 90 days.')
    else:
        method=chosen.method
        status=('affordable_with_plan' if chosen.changes or method in ('partial_payment','installments')
                else 'affordable_now' if method=='full_payment' else 'affordable_later')
        plan='|'.join(f'{d}:{fmt(a)}' for d,a in chosen.payments)
        changes='|'.join(c.text() for c in chosen.changes) or 'none'
        low=balances(forecast,chosen.payments,chosen.changes)[-1][2]
        explanation=(f'{method.replace("_"," ").capitalize()}: {len(chosen.payments)} payment(s), '
                     f'{profile["home_currency"]} {fmt(sum(a for _,a in chosen.payments))} total, '
                     f'completed {chosen.payments[-1][0]}. Lowest projected balance {fmt(low)} '
                     f'against minimum {fmt(forecast.minimum)}; safe today {fmt(safe)}.')
        if chosen.changes: explanation+=' Requires '+changes+'.'
    if forecast.notes:
        explanation+=' Evidence: '+forecast.notes[0][:180]
    row=dict(zip(['request_id','amount_safe_to_pay','affordability_status','recommended_payment_method',
                  'payment_plan','earliest_date_for_full_payment','spending_changes_needed','decision_explanation'],
                 [request['request_id'],fmt(safe),status,method,plan,str(earliest) if earliest else '',changes,explanation]))
    return row,chosen
