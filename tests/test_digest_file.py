import datetime as dt
import json
import unittest
from tests.helpers import Sandbox, FakeReviewer, FakeClock, verdict, codex_session, append_turn

class DigestFileTests(unittest.TestCase):
    def test_original_daily_layout(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Goal','Done.')])
            code,out,err=sb.cli(['watch','--only',str(sb.project)],reviewer=FakeReviewer([verdict(draft='Report the export result.')]))
            self.assertEqual(code,0,err)
            paths=list((sb.root/'state/sidecarwatch').glob('digest-*.md'))
            self.assertEqual(len(paths),1)
            for text in ('**WORKING**','checks:','kind: none','draft: Report the export result.'):
                self.assertIn(text,paths[0].read_text())

    def test_init_and_first_run_cutoff(self):
        with Sandbox() as sb:
            clock=FakeClock(); old=clock.now()-dt.timedelta(hours=1)
            p=codex_session(sb.codex,sb.project,[('Goal','Done.')],at=old)
            args=['watch','--only',str(sb.project)]
            fake=FakeReviewer([])
            self.assertEqual(sb.cli(args,reviewer=fake,clock=clock)[0],0)
            self.assertEqual(fake.calls,[])
            # Subsequent runs include an unseen completion older than 30 minutes.
            append_turn(p,'Same task','Tests passed.',old+dt.timedelta(minutes=1))
            fake=FakeReviewer([verdict()])
            self.assertEqual(sb.cli(args,reviewer=fake,clock=clock)[0],0)
            self.assertEqual(len(fake.calls),1)
            append_turn(p,'Same task','Verified.',clock.now())
            fake=FakeReviewer([])
            self.assertEqual(sb.cli(args+['--init'],reviewer=fake,clock=clock)[0],0)
            self.assertEqual(sb.cli(args,reviewer=fake,clock=clock)[0],0)
            self.assertEqual(fake.calls,[])
