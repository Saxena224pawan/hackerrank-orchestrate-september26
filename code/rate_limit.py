"""Cross-process pacing for all Gemini calls made from one checkout."""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:
            stream.write(b'0')
            stream.flush()
        if os.name=='nt':
            import msvcrt
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    break
                except OSError:
                    time.sleep(.1)
            try: yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class RateLimiter:
    # Small boundary margin avoids 16 calls being counted at a minute boundary.
    interval=60/15+.05

    def __init__(self,root,clock=None,sleep=None,announce=print):
        self.state=Path(root)/'.cache'/'gemini_rate_limit.json'
        self.lock=self.state.with_suffix('.lock')
        self.clock=clock or time.time
        self.sleep=sleep or time.sleep
        self.announce=announce

    def wait(self):
        with locked(self.lock):
            last=None
            if self.state.exists():
                try: last=float(json.loads(self.state.read_text())['last_started'])
                except (ValueError,KeyError,TypeError):
                    # A damaged limiter file must not permit a burst.
                    self.sleep(60)
            if last is not None:
                delay=last+self.interval-self.clock()
                if delay>0:
                    self.announce(f'Rate limit: sleeping {delay:.2f}s (maximum 15 API calls/minute).')
                while delay>0:
                    self.sleep(min(delay,60))
                    delay=last+self.interval-self.clock()
            # Reserve before sending. Failed API attempts also consume a slot.
            with self.state.open('w',encoding='utf-8') as stream:
                json.dump({'last_started':self.clock()},stream)
                stream.flush()
                os.fsync(stream.fileno())
