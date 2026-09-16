"""Append-only, redacted chat persistence and transcript export."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain import DataError


def redact(text):
    text=str(text)
    for key,value in os.environ.items():
        if len(value)>=8 and any(token in key.upper() for token in ('KEY','TOKEN','SECRET','PASSWORD','COOKIE')):
            text=text.replace(value,'[REDACTED]')
    text=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----','[REDACTED]',text,flags=re.S)
    text=re.sub(r'(?i)(?:bearer\s+)[A-Za-z0-9._~-]+','Bearer [REDACTED]',text)
    text=re.sub(r'\bAIza[A-Za-z0-9_-]{20,}\b','[REDACTED]',text)
    text=re.sub(r'(?i)\b(api[_ -]?key|token|password|secret|cookie)\s*[:=]\s*[^\s,;]+',r'\1=[REDACTED]',text)
    text=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[REDACTED EMAIL]',text)
    text=re.sub(r'\b(?:\d[ -]?){12,19}\b','[REDACTED ACCOUNT]',text)
    text=re.sub(r'\b\d{3}-\d{2}-\d{4}\b','[REDACTED ID]',text)
    text=re.sub(r'(?i)\b(phone|mobile|account|iban|address)\s*[:=]\s*[^\n;]+',r'\1=[REDACTED]',text)
    return text


def scrub(value):
    if isinstance(value,dict): return {k:scrub(v) for k,v in value.items()}
    if isinstance(value,list): return [scrub(v) for v in value]
    return redact(value) if isinstance(value,str) else value


class History:
    def __init__(self,root,user_id,session_id=None):
        self.session_id=session_id or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',self.session_id):
            raise DataError('Invalid session ID')
        self.user_id=user_id
        self.path=Path(root)/'chat_history'/f'{self.session_id}.jsonl'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.messages=[]
        self.warning=None
        self.corrupt=False
        if self.path.exists():
            lines=self.path.read_text(encoding='utf-8').splitlines()
            for i,line in enumerate(lines):
                try:
                    item=json.loads(line)
                    if not isinstance(item,dict) or item.get('role') not in ('user','assistant'):
                        raise ValueError('Invalid history record')
                except (json.JSONDecodeError,ValueError):
                    if i!=len(lines)-1:
                        raise DataError('Corrupt chat history before final record')
                    self.warning='Incomplete final history record preserved. Resume creates a continuation session.'
                    self.corrupt=True
                    break
                if item.get('user_id')!=user_id:
                    raise DataError('Session belongs to another user')
                self.messages.append(item)
        if self.corrupt:
            old=self.session_id
            self.session_id=uuid4().hex
            self.path=self.path.parent/f'{self.session_id}.jsonl'
            recovered=list(self.messages)
            self.messages=[]
            for message in recovered:
                self.append(message['role'],message['text'],message.get('request_id'),
                            message.get('decision'),message.get('request'))
            self.append('assistant',f'Continuation of session {old}; damaged original preserved.')

    def append(self,role,text,request_id=None,decision=None,request=None):
        row=scrub({'session_id':self.session_id,'timestamp':datetime.now(timezone.utc).isoformat(),
                   'role':role,'text':text,'user_id':self.user_id,'request_id':request_id,
                   'decision':decision,'request':request})
        with self.path.open('a',encoding='utf-8',newline='\n') as stream:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.messages.append(row)
        return row

    def export(self):
        target=self.path.with_suffix('.txt')
        target.write_text(render(self.messages),encoding='utf-8')
        return target


def render(messages):
    return '\n\n'.join(f'[{m["timestamp"]}] {m["role"]}\n{redact(m["text"])}' for m in messages)+'\n'


def export_all(root):
    root=Path(root)
    log=root/'log.txt'
    sections=['DEVELOPMENT CONVERSATION\nEarlier entries may contain summaries rather than verbatim responses.\n',
              redact(log.read_text(encoding='utf-8')) if log.exists() else 'No development log available.']
    for path in sorted((root/'chat_history').glob('*.jsonl')):
        messages=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try: messages.append(json.loads(line))
            except json.JSONDecodeError: break
        sections.extend([f'\nAPPLICATION SESSION: {path.stem}\n',render(messages)])
    target=root/'chat_transcript.txt'
    target.write_text('\n'.join(sections),encoding='utf-8')
    return target
