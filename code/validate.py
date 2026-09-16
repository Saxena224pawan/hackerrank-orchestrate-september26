"""Independent replay of serialized output before accepting a decision."""
from collections import defaultdict
from datetime import timedelta

from domain import COLUMNS, DataError, ZERO, choices, day, money


def require(condition, message):
    if not condition:
        raise DataError('Validation: '+message)


def validate_decision(row, request, profile, options, forecast):
    require(list(row)==COLUMNS,'wrong column order')
    require(row['request_id']==request['request_id'],'request ID mismatch')
    amount=money(request['requested_amount'])
    safe=money(row['amount_safe_to_pay'])
    require(safe<=amount,'safe amount exceeds request')
    status=row['affordability_status']
    method=row['recommended_payment_method']
    require(status in {'affordable_now','affordable_with_plan','affordable_later','not_affordable'},'status')
    require(method in {'full_payment','partial_payment','installments','wait','not_recommended'},'method')
    earliest=day(row['earliest_date_for_full_payment']) if row['earliest_date_for_full_payment'] else None
    if earliest:
        require(forecast.start<=earliest<=forecast.end,'earliest date outside forecast')
    payments=[]
    if row['payment_plan']!='none':
        for entry in row['payment_plan'].split('|'):
            d,a=entry.split(':')
            payments.append((day(d),money(a)))
    require(payments==sorted(payments),'payment chronology')
    require(all(forecast.start<=d<=min(forecast.end,day(request['desired_completion_date'])) for d,_ in payments),'payment outside deadline/horizon')
    if method=='not_recommended':
        require(not payments and row['spending_changes_needed']=='none','fallback contains payments or changes')
    else:
        require(bool(payments),'missing payment plan')
        require(('full_payment' if method=='wait' else method) in choices(profile['payment_methods_user_will_consider']),'user rejects method')
    if method in ('full_payment','wait'):
        require(len(payments)==1 and payments[0][1]==amount,'incorrect single payment')
        require((method=='full_payment')==(payments[0][0]==forecast.start),'single-payment method date')
    if status=='affordable_now':
        require(method=='full_payment' and safe==amount and earliest==forecast.start and row['spending_changes_needed']=='none','affordable_now contract')
    if method=='partial_payment':
        require(status=='affordable_with_plan' and request['allows_partial_payment']=='true','partial eligibility')
        require(ZERO<safe<amount and len(payments)==2,'partial count/bounds')
        require(payments==[(forecast.start,safe),(earliest,amount-safe)],'partial schedule mismatch')
    if method=='installments':
        require(status=='affordable_with_plan','installment status')
        matches=[]
        for o in options:
            if o['payment_method']!='installments': continue
            n=int(o['number_of_payments'])
            expected=[(day(o['first_payment_date'])+timedelta(days=i*int(o['payment_frequency_days'])),money(o['payment_amount'])) for i in range(n)]
            if expected==payments: matches.append(o)
        require(bool(matches),'installments do not match a supplied offer')
        require(bool(profile['max_installment_months']) and len(payments)<=int(profile['max_installment_months']),'installment preference limit')

    reductions={}
    if row['spending_changes_needed']!='none':
        require(status=='affordable_with_plan','spending change status')
        for text in row['spending_changes_needed'].split('|'):
            parts=text.split(':')
            require(len(parts) in (2,3),'malformed spending change')
            kind,eid=parts[:2]
            require(eid in forecast.adjustable and eid not in reductions,'nonrecurring or duplicated change')
            e=forecast.adjustable[eid]
            require(e['category'] not in choices(profile['expense_categories_to_protect']),'protected category')
            if kind=='stop':
                require(len(parts)==2 and 'stoppable' in e['flexibility'] and e['category'] in choices(profile['expense_categories_user_is_willing_to_stop']),'stop not permitted')
                reductions[eid]=ZERO
            else:
                require(kind=='reduce_to' and len(parts)==3 and 'reducible' in e['flexibility'] and e['category'] in choices(profile['expense_categories_user_is_willing_to_reduce']),'reduction not permitted')
                new=money(parts[2])
                bound=money(e['minimum_allowed_amount'])
                if e['currency']!=e['home_currency']:
                    bound*=e['_amount']/money(e['amount'])
                require(bound<=new<money(e['forecast_amount']),'reduction outside bounds')
                reductions[eid]=new
        require(len(reductions)<=3,'too many spending changes')
    # Replay independently from planner.balances and serialized changes/payments.
    entries=defaultdict(list)
    for f in forecast.flows:
        delta=f.amount
        if f.recurring and f.source in reductions and delta<0:
            delta=-reductions[f.source]
        entries[f.when].append(delta)
    pmap=defaultdict(list)
    for d,a in payments: pmap[d].append(a)
    balance=forecast.opening
    minimum=balance
    for d in sorted(set(entries)|set(pmap)):
        for delta in sorted(entries[d]):
            balance+=delta
            minimum=min(minimum,balance)
        for a in pmap[d]:
            balance-=a
            minimum=min(minimum,balance)
    if payments:
        require(minimum>=forecast.minimum,'plan breaches minimum balance')
    return True
