import os
import unittest
from unittest.mock import patch
from sidecarwatch.state import State
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, nudge, codex_session


class KillTests(unittest.TestCase):
    def test_kill_switch(self):
        with Sandbox() as sb:
            fake=FakeReviewer([])
            self.assertEqual(sb.cli(['stop'])[0],0)
            self.assertEqual(sb.cli(['watch','--only',str(sb.project)],reviewer=fake)[0],3)
            self.assertEqual(sb.cli(['resume'])[0],0)
            self.assertEqual(sb.cli(['resume'])[0],0)
            with patch.dict(os.environ,{'SIDECARWATCH_STOP':'1'}):
                self.assertEqual(sb.cli(['watch','--only',str(sb.project)],reviewer=fake)[0],3)
            self.assertEqual(fake.calls,[])
            clock=FakeClock()
            clock.on_sleep=lambda:sb.cli(['stop'])
            self.assertEqual(sb.cli(['watch','--only',str(sb.project),'--interval','60s'],clock=clock,reviewer=fake)[0],3)

    def test_stop_between_review_and_send(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Goal','Done')])
            def stop():
                sb.cli(['stop'])
                return nudge()
            d=FakeDelivery()
            self.assertEqual(sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live'],reviewer=FakeReviewer([stop]),delivery=d)[0],3)
            self.assertEqual(d.calls,[])

    def test_watch_lock_and_nested(self):
        with Sandbox() as sb:
            lock=State(sb.root/'state/sidecarwatch').acquire()
            try:
                self.assertEqual(sb.cli(['watch','--only',str(sb.project)])[0],6)
            finally:
                lock.close()
            with patch.dict(os.environ,{'SIDECARWATCH_ACTIVE':'1'}):
                self.assertEqual(sb.cli(['watch','--only',str(sb.project)])[0],2)
