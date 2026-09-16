import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App, Chat
from domain import ChatIntent, DataError, Evidence, Fact
from evidence import Gemini, resolve_unapproved_bonus
from test_engine import profile, request


def response(text='{"facts":[],"unresolved":[]}'):
    return SimpleNamespace(text=text,usage_metadata=SimpleNamespace(
        prompt_token_count=12,candidates_token_count=8,thoughts_token_count=2,
        cached_content_token_count=0,total_token_count=22))


class IntegrationTests(unittest.TestCase):
    def test_unapproved_bonus_is_not_missing_required_income(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            issue=Evidence(unresolved=['Quarterly performance bonus amount and payment date are pending final approval and confirmation from payroll.'])
            app.gemini.call=Mock(return_value=issue)
            result=app.gemini.extract(app.dataset,app.dataset.requests['request_65'])
            self.assertEqual(result,Evidence())
            self.assertEqual(app.gemini.call.call_count,1)

    def test_pending_bonus_does_not_clear_missing_expenses(self):
        result=Evidence(unresolved=['Bonus amount pending approval','Missing required rent amount'])
        messages=[{'message_text':'Quarterly bonus is subject to final review. Amount and date have not been approved.'}]
        corrected=resolve_unapproved_bonus(result,messages)
        self.assertEqual(corrected.unresolved,['Missing required rent amount'])

    def test_bonus_issue_requires_unapproved_source_evidence(self):
        result=Evidence(unresolved=['Bonus amount missing'])
        self.assertEqual(resolve_unapproved_bonus(result,[]),result)
        self.assertEqual(resolve_unapproved_bonus(result,[{'message_text':'Your bonus has been approved.'}]),result)

    def test_indonesian_pending_bonus_notice(self):
        result=Evidence(unresolved=['Quarterly bonus amount and date are unknown'])
        messages=[{'message_text':'Bonus kuartalan masih menunggu hasil penilaian. Jumlah akhir dan tanggal pembayaran belum disetujui.'}]
        self.assertEqual(resolve_unapproved_bonus(result,messages),Evidence())

    def test_live_adapter_and_cache_with_fake_transport(self):
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            g.client=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response())))
            self.assertEqual(g.call({'test':True},Evidence),Evidence())
            self.assertEqual(g.call({'test':True},Evidence),Evidence())
            self.assertEqual(g.client.models.generate_content.call_count,1)
            self.assertEqual([r['cached'] for r in g.usage],[False,True])
            self.assertEqual(g.usage[0]['total_tokens'],22)
            records=list((Path(root)/'.cache'/'api_responses').glob('*.json'))
            self.assertEqual(len(records),1)
            self.assertEqual(json.loads(records[0].read_text())['validation_status'],'valid')

    def test_json_schema_transport_preserves_strict_nested_objects(self):
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            call=Mock(return_value=response())
            g.client=SimpleNamespace(models=SimpleNamespace(generate_content=call))
            g.call({},Evidence)
            config=call.call_args.kwargs['config']
            self.assertIsNone(config.response_schema)
            schema=config.response_json_schema
            self.assertIs(schema['additionalProperties'],False)
            self.assertIs(schema['$defs']['Fact']['additionalProperties'],False)

    def test_sdk_http_payload_uses_response_json_schema(self):
        import httpx
        from google import genai
        from google.genai import types
        captured=[]
        def handler(req):
            captured.append(json.loads(req.content))
            return httpx.Response(200,json={
                'candidates':[{'content':{'role':'model','parts':[{'text':'{"facts":[],"unresolved":[]}'}]},'finishReason':'STOP'}],
                'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':8,'totalTokenCount':20}})
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            with httpx.Client(transport=httpx.MockTransport(handler)) as http:
                g.client=genai.Client(api_key='test-key',http_options=types.HttpOptions(httpx_client=http))
                g.call({},Evidence)
            config=captured[0]['generationConfig']
            self.assertNotIn('responseSchema',config)
            self.assertIs(config['responseJsonSchema']['additionalProperties'],False)
            self.assertNotIn('additional_properties',json.dumps(config))

    def test_bad_model_json_is_not_cached(self):
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            g.client=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response('not json'))))
            with self.assertRaises(DataError): g.call({},Evidence)
            self.assertEqual(list(g.cache.glob('*.json')),[])
            self.assertEqual(len(g.usage),1)  # Billed response is still counted.
            records=list((Path(root)/'.cache'/'api_responses').glob('*.json'))
            self.assertEqual(len(records),1)
            saved=json.loads(records[0].read_text())
            self.assertEqual(saved['text'],'not json')
            self.assertEqual(saved['validation_status'],'unvalidated')

    def test_transient_retries_are_bounded(self):
        class TemporaryError(Exception): code=429
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            g.limiter=Mock()
            call=Mock(side_effect=TemporaryError())
            g.client=SimpleNamespace(models=SimpleNamespace(generate_content=call))
            with patch('evidence.time.sleep') as sleep:
                with self.assertRaisesRegex(DataError,'HTTP 429'): g.call({},Evidence)
            self.assertEqual(call.call_count,4)
            self.assertEqual(sleep.call_count,3)
            self.assertEqual(g.limiter.wait.call_count,4)

    def test_permanent_errors_are_not_retried(self):
        class BadRequest(Exception): code=400; message='Bad schema'; status='INVALID_ARGUMENT'
        with tempfile.TemporaryDirectory() as root:
            g=Gemini(Path(root)); g.key='test-key'
            call=Mock(side_effect=BadRequest())
            g.client=SimpleNamespace(models=SimpleNamespace(generate_content=call))
            with self.assertRaisesRegex(DataError,'Bad schema'): g.call({},Evidence)
            self.assertEqual(call.call_count,1)

    def test_actual_batch_chat_agreement_and_resume(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True,mode='chat')
            # Public request_01 has no message/image evidence; run real engine.
            expected=app.evaluate(app.dataset.samples['request_01'])
            chat=Chat(app,'user_01','integration')
            text=chat.handle('/request request_01')
            self.assertIn(expected['decision_explanation'],text)
            self.assertEqual(chat.history.messages[-1]['decision'],expected)
            resumed=Chat(app,'user_01','integration')
            self.assertEqual(resumed.last_decision,expected)
            self.assertEqual(resumed.current['request_id'],'request_01')

    def test_chat_missing_fields_does_not_calculate(self):
        with tempfile.TemporaryDirectory() as root:
            app=SimpleNamespace(root=Path(root),dataset=SimpleNamespace(profiles={'u':profile()},requests={'r':request()},samples={}),
                                gemini=SimpleNamespace(intent=Mock(return_value=ChatIntent(intent='request',requested_amount='500'))),
                                evaluate=Mock())
            chat=Chat(app,'u','s')
            text=chat.handle('Can I spend 500?')
            self.assertIn('request_date',text)
            app.evaluate.assert_not_called()
            self.assertEqual(len(chat.history.messages),2)

    def test_informational_pending_payout_gets_one_evidence_review(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            initial=Evidence(unresolved=['No amount for pending platform payout'])
            app.gemini.call=Mock(side_effect=[initial,Evidence()])
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_47'])
            self.assertEqual(evidence.unresolved,[])
            self.assertEqual(app.gemini.call.call_count,2)
            reviewed=app.gemini.call.call_args
            self.assertEqual(reviewed.kwargs['purpose'],'evidence_review')
            self.assertTrue(reviewed.args[0]['messages'])

    def test_informational_childcare_notice_does_not_block(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            unresolved=Evidence(unresolved=['childcare payment amount'])
            app.gemini.call=Mock(side_effect=[unresolved,unresolved])
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_83'])
            self.assertEqual(evidence.unresolved,[])
            self.assertEqual(app.gemini.call.call_count,2)

    def test_unlinked_message_cannot_change_event_direction(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            fact=Fact(source_ids=['message_74'],event_id='event_9080',category='salary',
                      action='confirm',amount='1804',currency='EUR',
                      effective_date='2025-08-15',direction='credit',
                      status='scheduled',recurring=True,note='unsupported link')
            app.gemini.call=Mock(return_value=Evidence(facts=[fact]))
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_98'])
            self.assertEqual(evidence.facts,[])

    def test_unrealized_no_cash_message_cannot_become_debit(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            fact=Fact(source_ids=['message_185'],event_id='event_21785',
                      category='investment',action='amend',amount='2690400',
                      currency='IDR',effective_date='2025-02-01',
                      direction='debit',status='unrealized',
                      note='No cash transaction')
            app.gemini.call=Mock(return_value=Evidence(facts=[fact]))
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_236'])
            self.assertEqual(evidence.facts,[])

    def test_incomplete_childcare_add_fact_does_not_reach_ledger(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            fact=Fact(source_ids=['message_120'],category='childcare',action='add',
                      recurring=True,note='amount not supplied')
            app.gemini.call=Mock(return_value=Evidence(facts=[fact]))
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_155'])
            self.assertEqual(evidence.facts,[])

    def test_unquantified_card_minimum_notice_does_not_block(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            missing=Evidence(unresolved=[
                'Minimum payment due amounts for two separate card accounts mentioned in message_136'])
            app.gemini.call=Mock(side_effect=[missing,missing])
            evidence=app.gemini.extract(app.dataset,app.dataset.requests['request_172'])
            self.assertEqual(evidence, Evidence())
            self.assertEqual(app.gemini.call.call_count,2)

    def test_required_missing_evidence_still_blocks_after_review(self):
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as root:
            app=App(Path(root),repo/'dataset',offline=True)
            missing=Evidence(unresolved=['Cannot read required bill amount'])
            app.gemini.call=Mock(return_value=missing)
            with self.assertRaisesRegex(DataError,'required bill amount'):
                app.gemini.extract(app.dataset,app.dataset.requests['request_47'])
            self.assertEqual(app.gemini.call.call_count,2)


if __name__=='__main__': unittest.main()
