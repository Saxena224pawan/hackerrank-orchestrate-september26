import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rate_limit import RateLimiter


class Clock:
    def __init__(self):
        self.now=1000.
        self.sleeps=[]
    def time(self): return self.now
    def sleep(self,seconds):
        self.sleeps.append(seconds)
        self.now+=seconds


class RateLimitTests(unittest.TestCase):
    def test_at_most_fifteen_calls_in_any_minute(self):
        with tempfile.TemporaryDirectory() as root:
            clock=Clock()
            limiter=RateLimiter(root,clock.time,clock.sleep,lambda _:None)
            starts=[]
            for _ in range(31):
                limiter.wait()
                starts.append(clock.time())
            self.assertEqual(len(clock.sleeps),30)
            self.assertTrue(all(b-a>=4 for a,b in zip(starts,starts[1:])))
            for start in starts:
                self.assertLessEqual(sum(start<=t<start+60 for t in starts),15)

    def test_new_instance_obeys_persisted_last_call(self):
        with tempfile.TemporaryDirectory() as root:
            clock=Clock()
            RateLimiter(root,clock.time,clock.sleep,lambda _:None).wait()
            RateLimiter(root,clock.time,clock.sleep,lambda _:None).wait()
            self.assertEqual(len(clock.sleeps),1)
            self.assertGreaterEqual(clock.sleeps[0],4)

    def test_slow_request_needs_no_extra_sleep(self):
        with tempfile.TemporaryDirectory() as root:
            clock=Clock()
            limiter=RateLimiter(root,clock.time,clock.sleep,lambda _:None)
            limiter.wait()
            clock.now+=10
            limiter.wait()
            self.assertEqual(clock.sleeps,[])


if __name__=='__main__': unittest.main()
