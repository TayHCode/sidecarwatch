import json
import unittest
from tests.helpers import Sandbox, claude_session, codex_session, FakeReviewer, FakeDelivery, verdict, nudge


class AuditTests(unittest.TestCase):
    def test_failed_reviews_report_one_error_and_never_deliver(self):
        from sidecarwatch.reviewer import ReviewError
        failures = [ReviewError('CLI unavailable'), ReviewError('reviewer failed'), ReviewError('CLI timed out'), ReviewError('invalid reviewer answer'), {'summary': 'incomplete'}]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), Sandbox() as sb:
                codex_session(sb.codex, sb.project, [('Goal','Done')])
                delivery = FakeDelivery()
                code, out, err = sb.cli(['audit','codex'], reviewer=FakeReviewer([failure]), delivery=delivery)
                self.assertEqual(code, 5)
                self.assertEqual(len([line for line in err.splitlines() if not line.startswith('Sending')]), 1)
                self.assertEqual(delivery.calls, [])
                fake = FakeReviewer([failure]*3)
                args = ['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live']
                code, out, err = sb.cli(args, reviewer=fake, delivery=delivery)
                self.assertEqual(code, 5)
                self.assertIn('Could not review', out)
                self.assertEqual(delivery.calls, [])
                self.assertEqual(sb.cli(args, reviewer=fake, delivery=delivery)[0], 5)
                self.assertEqual(sb.cli(args, reviewer=fake, delivery=delivery)[0], 5)
                self.assertEqual(sb.cli(args, reviewer=fake, delivery=delivery)[0], 10)
                self.assertIn('Needs you', (sb.root/'state/sidecarwatch/needs-you.md').read_text())
                self.assertEqual(len(fake.calls), 3)

    def test_audit_report(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Add CSV export','Checking export.')],running=True)
            for correction in ['no','maybe','yes']:
                code,out,err=sb.cli(['audit','codex'],reviewer=FakeReviewer([verdict(should_correct=correction)]))
                self.assertEqual(code,0 if correction=='no' else 10)
                for section in ['What it is doing','On task','In scope','Simple','Should you correct it?']:
                    self.assertIn(section,out)
            code,out,err=sb.cli(['audit','codex','--json'],reviewer=FakeReviewer([nudge(reply_kind='needs_user')]))
            self.assertEqual(code,10)
            self.assertEqual(json.loads(out)['reviewer'],'claude')
            fake=FakeReviewer([])
            self.assertEqual(sb.cli(['audit','codex','--list-only'],reviewer=fake)[0],0)
            self.assertEqual(fake.calls,[])

    def test_audit_reviewer_auto(self):
        with Sandbox() as sb:
            claude_session(sb.claude,sb.project,[('Goal','Done.')])
            codex_session(sb.codex,sb.project,[('Goal','Done.')])
            (sb.fake/'verdict.json').write_text(json.dumps(verdict()))
            for agent,other in [('claude','codex'),('codex','claude')]:
                code,out,err=sb.cli(['audit',agent,'--json'])
                self.assertEqual(code,0,err)
                self.assertEqual(json.loads(out)['reviewer'],other)
            (sb.root/'bin/claude').unlink()
            code,out,err=sb.cli(['audit','codex'])
            self.assertEqual(code,5)
            self.assertIn('same CLI',err)
            self.assertEqual(len([line for line in err.splitlines() if not line.startswith('Sending')]),1)

    def test_garbage_file(self):
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[])
            with p.open('a') as f:
                f.write('broken\n'*5)
            self.assertIn('unknown?',sb.cli(['sessions'])[1])
            code,out,err=sb.cli(['audit','codex'])
            self.assertEqual(code,5)
            self.assertIn('format-not-recognised',err)
