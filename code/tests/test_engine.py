import csv
import json
import os
import sys
import tempfile
import struct
import zlib
import unittest
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from domain import DataError, Evidence, Fact, Flow, Forecast, money
from data import Dataset
from evidence import Gemini, api_error, probe_png
from history import History, export_all, redact
from ledger import reconstruct
from planner import capacity, decide, feasible
from validate import validate_decision

START=date(2026,1,1)


def profile(**changes):
    value={'user_id':'u','home_currency':'USD','current_available_balance':'1000',
           'minimum_balance_to_keep':'100','financial_priorities':'essentials',
           'expense_categories_to_protect':'rent',
           'expense_categories_user_is_willing_to_reduce':'streaming',
           'expense_categories_user_is_willing_to_stop':'streaming',
           'payment_methods_user_will_consider':'full_payment|partial_payment|installments',
           'max_installment_months':'3'}
    value.update(changes)
    return value


def request(**changes):
    value={'request_id':'r','user_id':'u','request_date':str(START),
           'desired_completion_date':'2026-03-31','requested_amount':'500',
           'allows_partial_payment':'true','request_type':'purchase','request_text':'A purchase'}
    value.update(changes)
    return value


def forecast(flows=(),opening='1000',minimum='100'):
    return Forecast(START,START+timedelta(days=90),D(opening),D(minimum),list(flows))


def event(eid,**changes):
    value={'event_id':eid,'user_id':'u','event_type':'expense','description':'Rent',
           'category':'rent','direction':'debit','amount':'100','currency':'USD',
           'event_date':'2025-12-01','settlement_date':'2025-12-01','status':'settled',
           'linked_event_id':'','flexibility':'fixed','minimum_allowed_amount':''}
    value.update(changes)
    return value


def fake_dataset(events,p=None):
    obj=SimpleNamespace(profiles={'u':p or profile()},by_user={'financial_events':{'u':events}})
    obj.convert=lambda a,c,h,d: money(a) if c==h else (_ for _ in ()).throw(DataError('Missing FX'))
    return obj


class FinancialTests(unittest.TestCase):
    def test_future_expenses_limit_safe_today(self):
        f=forecast([Flow(START+timedelta(days=20),D('-600'),'rent','rent')])
        self.assertEqual(capacity(f,D('500')),(D('300'),None))

    def test_full_payment(self):
        f=forecast()
        row,_=decide(f,request(),profile(),[])
        self.assertEqual(row['affordability_status'],'affordable_now')
        self.assertTrue(validate_decision(row,request(),profile(),[],f))

    def test_partial_two_payments(self):
        payday=START+timedelta(days=15)
        f=forecast([Flow(payday,D('500'),'salary','salary')],opening='300')
        p=profile(payment_methods_user_will_consider='partial_payment')
        row,_=decide(f,request(),p,[])
        self.assertEqual(row['payment_plan'],'2026-01-01:200|2026-01-16:300')
        self.assertTrue(validate_decision(row,request(),p,[],f))

    def test_partial_forbidden(self):
        f=forecast([Flow(START+timedelta(days=15),D('500'),'salary','salary')],opening='300')
        p=profile(payment_methods_user_will_consider='partial_payment')
        row,_=decide(f,request(allows_partial_payment='false'),p,[])
        self.assertEqual(row['recommended_payment_method'],'not_recommended')

    def test_deadline(self):
        f=forecast([Flow(START+timedelta(days=15),D('500'),'salary','salary')],opening='300')
        row,_=decide(f,request(desired_completion_date='2026-01-10'),profile(),[])
        self.assertEqual(row['payment_plan'],'none')
        self.assertEqual(row['earliest_date_for_full_payment'],'2026-01-16')

    def test_income_does_not_hide_earlier_breach(self):
        f=forecast([Flow(START+timedelta(days=2),D('-950'),'e','rent'),
                    Flow(START+timedelta(days=3),D('2000'),'s','salary')])
        self.assertFalse(feasible(f))
        self.assertEqual(capacity(f,D('500')),(D('0'),None))

    def test_same_day_debit_precedes_salary(self):
        f=forecast([Flow(START,D('-950'),'e','rent'),Flow(START,D('2000'),'s','salary')])
        self.assertFalse(feasible(f))

    def test_installments_exact_schedule_and_fees(self):
        f=forecast()
        p=profile(payment_methods_user_will_consider='installments')
        o={'payment_option_id':'o1','request_id':'r','payment_method':'installments',
           'payment_amount':'175','number_of_payments':'3','first_payment_date':'2026-01-02',
           'payment_frequency_days':'28','financing_fee':'25','total_payable_amount':'525'}
        row,_=decide(f,request(),p,[o])
        self.assertEqual(row['payment_plan'],'2026-01-02:175|2026-01-30:175|2026-02-27:175')
        self.assertTrue(validate_decision(row,request(),p,[o],f))
        row['payment_plan']=row['payment_plan'].replace('175','170')
        with self.assertRaises(DataError): validate_decision(row,request(),p,[o],f)

    def test_lower_cost_offer_wins(self):
        f=forecast()
        p=profile(payment_methods_user_will_consider='installments')
        base={'request_id':'r','payment_method':'installments','number_of_payments':'2',
              'first_payment_date':'2026-01-02','payment_frequency_days':'28'}
        offers=[dict(base,payment_option_id='o1',payment_amount='275'),dict(base,payment_option_id='o2',payment_amount='250')]
        _,chosen=decide(f,request(),p,offers)
        self.assertEqual(chosen.option_id,'o2')

    def test_protected_and_flexible_changes(self):
        f=forecast([Flow(START,D('-100'),'sub','streaming',True)],opening='600')
        e=event('sub',category='streaming',flexibility='reducible_or_stoppable',minimum_allowed_amount='50')
        e.update(forecast_amount='100',home_currency='USD')
        f.adjustable={'sub':e}
        row,_=decide(f,request(),profile(),[])
        self.assertEqual(row['affordability_status'],'affordable_with_plan')
        self.assertEqual(row['spending_changes_needed'],'stop:sub')
        self.assertTrue(validate_decision(row,request(),profile(),[],f))
        p=profile(expense_categories_to_protect='rent|streaming')
        row,_=decide(f,request(),p,[])
        self.assertEqual(row['payment_plan'],'none')

    def test_pending_credit_excluded_debit_reserved(self):
        e=[event('credit',direction='credit',category='refund',status='pending',amount='900',settlement_date='2026-01-05'),
           event('debit',status='pending',amount='250',settlement_date='2026-01-15')]
        f=reconstruct(fake_dataset(e),request(),Evidence())
        self.assertEqual([(x.when,x.amount) for x in f.flows],[(START,D('-250'))])

    def test_settled_history_not_replayed(self):
        f=reconstruct(fake_dataset([event('e')]),request(),Evidence())
        self.assertEqual(f.flows,[])
        self.assertEqual(f.opening,D('1000'))

    def test_cancelled_lifecycle_not_reserved(self):
        e=[event('old',status='pending'),event('new',status='settled',linked_event_id='old')]
        self.assertEqual(reconstruct(fake_dataset(e),request(),Evidence()).flows,[])

    def test_distinct_investment_legs_not_deduplicated(self):
        e=[event('buy',event_type='investment_purchase',category='investment',status='scheduled',settlement_date='2026-01-05'),
           event('sale',event_type='investment_sale',direction='credit',category='investment',status='settled',linked_event_id='buy',settlement_date='2026-01-06')]
        f=reconstruct(fake_dataset(e),request(),Evidence())
        self.assertEqual(len(f.flows),2)

    def test_unrealized_is_not_cash(self):
        e=event('e',status='unrealized',direction='non_cash',category='investment')
        self.assertEqual(reconstruct(fake_dataset([e]),request(),Evidence()).flows,[])

    def test_blank_amount_errors_until_image_fact(self):
        e=event('e',amount='',status='pending',settlement_date='2026-01-05')
        with self.assertRaises(DataError): reconstruct(fake_dataset([e]),request(),Evidence())
        facts=Evidence(facts=[Fact(source_ids=['image_1'],event_id='e',action='amend',amount='125',note='Bill total')])
        self.assertEqual(reconstruct(fake_dataset([e]),request(),facts).flows[0].amount,D('-125'))

    def test_salary_delay(self):
        e=event('s',direction='credit',category='salary',status='scheduled',amount='1000',settlement_date='2026-01-15')
        facts=Evidence(facts=[Fact(source_ids=['m'],category='salary',action='amend',effective_date='2026-01-20',note='Payroll delayed')])
        self.assertEqual(reconstruct(fake_dataset([e]),request(),facts).flows[0].when,date(2026,1,20))

    def test_final_payroll_not_repeated(self):
        es=[event('s1',direction='credit',category='salary',description='Payroll credit',amount='1000',settlement_date='2025-10-15'),
            event('s2',direction='credit',category='salary',description='Payroll credit',amount='1000',settlement_date='2025-11-15'),
            event('s3',direction='credit',category='salary',description='Final employer payroll',amount='1000',settlement_date='2025-12-15')]
        self.assertEqual(reconstruct(fake_dataset(es),request(),Evidence()).flows,[])

    def test_platform_earnings_not_forecast_as_regular_salary(self):
        es=[event('s1',direction='credit',category='salary',description='Driver platform payout',amount='1000',settlement_date='2025-10-15'),
            event('s2',direction='credit',category='salary',description='Driver platform payout',amount='1100',settlement_date='2025-11-15'),
            event('s3',direction='credit',category='salary',description='Driver platform payout',amount='900',settlement_date='2025-12-15')]
        self.assertEqual(reconstruct(fake_dataset(es),request(),Evidence()).flows,[])

    def test_missing_fx_errors(self):
        e=event('e',currency='EUR',status='pending')
        with self.assertRaises(DataError): reconstruct(fake_dataset([e]),request(),Evidence())

    def test_negative_and_nan_rejected(self):
        for x in ('-1','NaN','Infinity',''):
            with self.assertRaises(DataError): money(x)


class PersistenceTests(unittest.TestCase):
    def test_probe_png_chunk_checksums_and_pixels(self):
        blob=probe_png()
        self.assertEqual(blob[:8],b'\x89PNG\r\n\x1a\n')
        cursor=8
        kinds=[]
        while cursor<len(blob):
            size=struct.unpack('>I',blob[cursor:cursor+4])[0]
            kind=blob[cursor+4:cursor+8]
            data=blob[cursor+8:cursor+8+size]
            crc=struct.unpack('>I',blob[cursor+8+size:cursor+12+size])[0]
            self.assertEqual(crc,zlib.crc32(kind+data)&0xffffffff)
            kinds.append(kind)
            if kind==b'IDAT': self.assertEqual(len(zlib.decompress(data)),64*(1+64*3))
            cursor+=size+12
        self.assertEqual(kinds,[b'IHDR',b'IDAT',b'IEND'])

    def test_provider_error_keeps_status_redacts_key(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'private_key_value'}):
            error=SimpleNamespace(code=400,status='INVALID_ARGUMENT',message='Invalid image; key=private_key_value')
            text=api_error(error,'preflight','gemini-3.1-flash-lite')
            self.assertIn('HTTP 400',text)
            self.assertIn('Invalid image',text)
            self.assertNotIn('private_key_value',text)

    def test_permission_error_explains_key_and_model_access(self):
        text=api_error(PermissionError('denied'),'evidence','gemini-3.5-flash-lite')
        self.assertIn('GEMINI_API_KEY',text)
        self.assertIn('permitted to use GEMINI_MODEL',text)

    def test_resume_and_export(self):
        with tempfile.TemporaryDirectory() as root:
            h=History(root,'u','session')
            h.append('user','Can I spend 500?')
            h.append('assistant','Yes',decision={'amount_safe_to_pay':'500'},request=request())
            resumed=History(root,'u','session')
            self.assertEqual(len(resumed.messages),2)
            self.assertEqual(resumed.messages[-1]['request']['request_id'],'r')
            self.assertIn('Can I spend 500?',resumed.export().read_text())
            self.assertIn('APPLICATION SESSION',export_all(root).read_text())

    def test_truncated_tail_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            h=History(root,'u','s')
            h.append('user','Hi')
            with h.path.open('a') as f: f.write('{"partial":')
            old=h.path.read_bytes()
            resumed=History(root,'u','s')
            self.assertTrue(resumed.warning)
            resumed.append('assistant','Hello')
            self.assertEqual(h.path.read_bytes(),old)
            again=History(root,'u',resumed.session_id)
            self.assertEqual(again.messages[0]['text'],'Hi')

    def test_user_isolation_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            h=History(root,'u','s'); h.append('user','Hi')
            with self.assertRaises(DataError): History(root,'other','s')
            with self.assertRaises(DataError): History(root,'u','../escape')

    def test_redaction(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'secret_value_123'}):
            s=redact('secret_value_123 email me at a@example.com account=1234567890123456')
            self.assertNotIn('secret_value_123',s)
            self.assertNotIn('a@example.com',s)
            self.assertNotIn('1234567890123456',s)

    def test_offline_uncached_model_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root),offline=True)
            with self.assertRaises(DataError): g.call({},Evidence)

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError): Evidence.model_validate({'affordability_status':'affordable_now'})


if __name__=='__main__': unittest.main()
