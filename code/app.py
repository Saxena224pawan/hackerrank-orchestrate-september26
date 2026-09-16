"""Shared batch/chat service and interactive command handling."""
import csv
import json
from pathlib import Path

from data import Dataset
from domain import COLUMNS, DataError
from evidence import Gemini, atomic_json
from history import History
from ledger import reconstruct
from planner import decide
from validate import validate_decision


class App:
    def __init__(self,root,dataset=None,offline=False,mode='batch'):
        self.root=Path(root)
        self.dataset=Dataset(dataset or self.root/'dataset')
        self.gemini=Gemini(self.root,mode=mode,offline=offline)

    def evaluate(self,request):
        self.dataset.validate_request(request)
        evidence=self.gemini.extract(self.dataset,request)
        forecast=reconstruct(self.dataset,request,evidence)
        profile=self.dataset.profiles[request['user_id']]
        options=self.dataset.options[request['request_id']]
        row,chosen=decide(forecast,request,profile,options)
        validate_decision(row,request,profile,options,forecast)
        return row

    def batch(self,samples=False,diagnose=False,output=None):
        requests=self.dataset.samples if samples else self.dataset.requests
        results=[]
        errors=[]
        for i,request in enumerate(requests.values(),1):
            try:
                results.append(self.evaluate(request))
            except DataError as exc:
                errors.append({'request_id':request['request_id'],'error':str(exc)})
                if not diagnose:
                    break
            if i%25==0:
                print(f'Processed {i}/{len(requests)}; valid={len(results)}, unresolved={len(errors)}',flush=True)
        complete=len(results)==len(requests) and not errors
        report_name='sample_usage_report.md' if samples else 'usage_report.md'
        report=self.root/'code'/'evaluation'/report_name
        self.gemini.report(report,len(results),complete,errors)
        atomic_json(self.root/'code'/'evaluation'/('sample_results.json' if samples else 'diagnostics.json'),
                    {'complete':complete,'results':results,'errors':errors})
        if samples:
            metrics={}
            for col in COLUMNS[1:-1]:
                matches=sum(row[col]==requests[row['request_id']][col] for row in results)
                metrics[col]={'matches':matches,'evaluated':len(results),'total':len(requests)}
            print(json.dumps(metrics,indent=2))
        elif complete:
            target=Path(output) if output else self.root/'output.csv'
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_suffix('.csv.tmp')
            with temp.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=COLUMNS,lineterminator='\n')
                writer.writeheader()
                writer.writerows(results)
            temp.replace(target)
            print(f'Validated {len(results)} rows -> {target}')
        if not complete:
            print(f'Run incomplete: {len(results)}/{len(requests)} verified. No submission CSV written.')
            if errors: print(errors[0]['error'])
        return complete


class Chat:
    def __init__(self,app,user_id,session_id=None):
        if user_id not in app.dataset.profiles:
            raise DataError('Unknown user ID')
        self.app=app
        self.user_id=user_id
        self.history=History(app.root,user_id,session_id)
        self.current=None
        self.last_decision=None
        for m in self.history.messages:
            rid=m.get('request_id')
            if rid:
                self.current=app.dataset.requests.get(rid) or app.dataset.samples.get(rid)
            if m.get('request'):
                self.current=m['request']
            if m.get('decision'): self.last_decision=m['decision']

    def handle(self,text):
        self.history.append('user',text,self.current['request_id'] if self.current else None)
        try:
            response,decision=self._handle(text.strip())
        except (DataError,ValueError) as exc:
            response,decision=f'Unable to calculate: {exc}',None
        self.history.append('assistant',response,self.current['request_id'] if self.current else None,decision,self.current)
        if decision: self.last_decision=decision
        return response

    def _handle(self,text):
        if text=='/exit': return 'Chat saved. Goodbye.',None
        if text=='/history':
            return '\n'.join(f'{m["role"]}: {m["text"]}' for m in self.history.messages[:-1]),None
        if text=='/export': return f'Transcript exported to {self.history.export()}',None
        if text.startswith('/request '):
            rid=text.split(maxsplit=1)[1]
            r=self.app.dataset.requests.get(rid) or self.app.dataset.samples.get(rid)
            if not r or r['user_id']!=self.user_id:
                raise DataError('Request not found for selected user')
            self.current=dict(r)
            decision=self.app.evaluate(self.current)
            return describe(decision),decision
        if text.startswith('/'):
            return 'Commands: /request <id>, /history, /export, /exit.',None
        intent=self.app.gemini.intent(text,[{'role':m['role'],'text':m['text']} for m in self.history.messages],self.current)
        if intent.intent=='explain':
            if self.last_decision: return describe(self.last_decision),self.last_decision
            return 'Select a request with /request <id>, or provide an amount, request date and completion date.',None
        if intent.intent=='help':
            return 'I can assess an expense using this user’s supplied finances. Provide the amount, request date, and deadline, or use /request <id>.',None
        missing=[name for name in ('requested_amount','request_date','desired_completion_date') if getattr(intent,name) is None]
        if missing:
            return 'Please provide '+', '.join(missing)+'. Dates must be YYYY-MM-DD; amounts use your home currency.',None
        self.current={'request_id':'hypothetical_'+self.history.session_id,'user_id':self.user_id,
                      'requested_amount':intent.requested_amount,'request_date':intent.request_date,
                      'desired_completion_date':intent.desired_completion_date,'request_type':intent.request_type,
                      'allows_partial_payment':'true' if intent.allows_partial_payment else 'false',
                      'request_text':intent.request_text or text}
        # A profile balance is not a live balance; a hypothetical date must share
        # the supplied profile's evaluation date to keep the opening balance valid.
        original_dates={r['request_date'] for r in list(self.app.dataset.requests.values())+list(self.app.dataset.samples.values()) if r['user_id']==self.user_id}
        if self.current['request_date'] not in original_dates:
            raise DataError('This profile snapshot supports request date(s): '+', '.join(sorted(original_dates)))
        decision=self.app.evaluate(self.current)
        return describe(decision),decision

    def run(self):
        print(f'Buy or Wait? Session {self.history.session_id}. Type /exit to save and quit.')
        if self.history.warning: print(self.history.warning)
        try:
            while True:
                text=input('You: ')
                print('Agent: '+self.handle(text))
                if text.strip()=='/exit': break
        except (EOFError,KeyboardInterrupt):
            print('\nChat saved.')
        finally:
            self.app.gemini.report(self.history.path.with_suffix('.usage.md'),0,True)


def describe(row):
    return (row['decision_explanation']+'\n'
            f'Status: {row["affordability_status"]}\n'
            f'Payment plan: {row["payment_plan"]}\n'
            f'Earliest full payment: {row["earliest_date_for_full_payment"] or "not within forecast"}\n'
            f'Spending changes: {row["spending_changes_needed"]}')
