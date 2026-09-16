"""Read only the published dataset and validate joins before computation."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from domain import DataError, day, money


SCHEMAS = {
    'financial_profiles': 'user_id home_currency current_available_balance minimum_balance_to_keep financial_priorities expense_categories_to_protect expense_categories_user_is_willing_to_reduce expense_categories_user_is_willing_to_stop payment_methods_user_will_consider max_installment_months',
    'financial_events': 'event_id user_id event_type description category direction amount currency event_date settlement_date status linked_event_id flexibility minimum_allowed_amount',
    'requests': 'request_id user_id request_date request_type requested_amount desired_completion_date allows_partial_payment request_text',
    'sample_requests': 'request_id user_id request_date request_type requested_amount desired_completion_date allows_partial_payment request_text',
    'request_payment_options': 'payment_option_id request_id payment_method payment_amount number_of_payments first_payment_date payment_frequency_days financing_fee total_payable_amount',
    'messages': 'message_id user_id request_id related_event_id sent_at source_type message_text',
    'images': 'image_id user_id request_id related_event_id',
    'exchange_rates': 'rate_date from_currency to_currency rate',
}


class Dataset:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.tables = {}
        for name, required in SCHEMAS.items():
            with (self.path / f'{name}.csv').open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if not set(required.split()) <= set(reader.fieldnames or []):
                    raise DataError(f'{name}: missing required columns')
                self.tables[name] = list(reader)
        self.profiles = self.index('financial_profiles', 'user_id')
        self.events = self.index('financial_events', 'event_id')
        self.requests = self.index('requests', 'request_id')
        self.samples = self.index('sample_requests', 'request_id')
        self.index('request_payment_options', 'payment_option_id')
        self.index('messages', 'message_id')
        self.index('images', 'image_id')
        self.by_user = {}
        for name in ('financial_events', 'messages', 'images'):
            groups = defaultdict(list)
            for row in self.tables[name]:
                if row['user_id'] not in self.profiles:
                    raise DataError(f'{name}: unknown user')
                groups[row['user_id']].append(row)
            self.by_user[name] = groups
        self.options = defaultdict(list)
        for row in self.tables['request_payment_options']:
            self.options[row['request_id']].append(row)
        self.rates = {}
        for row in self.tables['exchange_rates']:
            day(row['rate_date'])
            key = (row['rate_date'], row['from_currency'], row['to_currency'])
            rate = money(row['rate'])
            if rate <= 0 or key in self.rates:
                raise DataError('Invalid or duplicate exchange rate')
            self.rates[key] = rate
        self.validate()

    def index(self, name, key):
        out = {}
        for row in self.tables[name]:
            if not row[key] or row[key] in out:
                raise DataError(f'{name}: duplicate or empty {key}')
            out[row[key]] = row
        return out

    def validate(self):
        for p in self.profiles.values():
            money(p['current_available_balance'])
            money(p['minimum_balance_to_keep'])
        all_requests = self.samples | self.requests
        for r in all_requests.values():
            self.validate_request(r)
            if r['user_id'] not in self.profiles:
                raise DataError('Unknown request user')
        for e in self.events.values():
            day(e['event_date'])
            if e['settlement_date']:
                day(e['settlement_date'])
            if e['amount']:
                money(e['amount'])
            if e['status'] not in {'settled', 'pending', 'scheduled', 'unrealized', 'failed', 'cancelled'}:
                raise DataError('Unknown cash status')
            if e['direction'] not in {'credit', 'debit', 'non_cash'}:
                raise DataError('Unknown cash direction')
            link = e['linked_event_id']
            if link and (link not in self.events or self.events[link]['user_id'] != e['user_id']):
                raise DataError('Invalid event lifecycle link')
        for name in ('messages', 'images'):
            for row in self.tables[name]:
                eid, rid = row['related_event_id'], row['request_id']
                if eid and (eid not in self.events or self.events[eid]['user_id'] != row['user_id']):
                    raise DataError('Invalid evidence event link')
                if rid and (rid not in all_requests or all_requests[rid]['user_id'] != row['user_id']):
                    raise DataError('Invalid evidence request link')
                if name == 'images' and not self.image_path(row).is_file():
                    raise DataError(f'Missing image {row["image_id"]}')
        for option in self.tables['request_payment_options']:
            if option['request_id'] not in all_requests:
                raise DataError('Unknown payment-option request')
            day(option['first_payment_date'])
            count = int(option['number_of_payments'])
            if count < 1:
                raise DataError('Invalid payment count')
            for key in ('payment_amount', 'financing_fee', 'total_payable_amount'):
                money(option[key])
            if abs(money(option['payment_amount']) * count - money(option['total_payable_amount'])) > money('.01') * count:
                raise DataError('Payment option totals disagree')
            if count > 1 and int(option['payment_frequency_days'] or '0') <= 0:
                raise DataError('Invalid payment interval')

    @staticmethod
    def validate_request(r):
        money(r['requested_amount'])
        if day(r['desired_completion_date']) < day(r['request_date']):
            raise DataError('Completion date precedes request date')
        if r['allows_partial_payment'] not in ('true', 'false'):
            raise DataError('Invalid partial-payment flag')

    def image_path(self, row):
        path = self.path / 'media' / 'images' / (row['image_id'] + '.png')
        if path.resolve().parent != (self.path / 'media' / 'images').resolve():
            raise DataError('Invalid image identifier')
        return path

    def convert(self, amount, currency, home, when):
        amount = money(amount)
        if currency == home:
            return amount
        key = (str(when), currency, home)
        if key not in self.rates:
            raise DataError(f'Missing dated FX rate: {key}')
        return amount * self.rates[key]
