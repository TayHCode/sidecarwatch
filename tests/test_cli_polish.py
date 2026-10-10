import unittest
from tests.helpers import Sandbox, codex_session, FakeClock, FakeReviewer, FakeDelivery


class CliPolishTests(unittest.TestCase):
    def test_abbreviated_safety_flags_are_rejected(self):
        with Sandbox() as sb:
            reviewer, delivery = FakeReviewer([]), FakeDelivery()
            for flag in ('--liv', '--auto-repl', '--over', '--allo'):
                code, out, err = sb.cli(['watch','--only',str(sb.project),flag], reviewer=reviewer, delivery=delivery)
                self.assertEqual(code, 2, flag)
            self.assertFalse(reviewer.calls)
            self.assertFalse(delivery.calls)

    def test_help_and_empty_listing_explain_behavior(self):
        with Sandbox() as sb:
            self.assertIn('No sessions', sb.cli(['sessions'])[1])
            code, out, err = sb.cli(['watch','--help'])
            self.assertEqual(code, 0)
            for phrase in ('minimum 60s', 'default off', 'default dry-run', 'ceiling 50', 'human questions'):
                self.assertIn(phrase, ' '.join(out.split()))
            self.assertIn('preserve intact counts', ' '.join(sb.cli(['rearm','--help'])[1].split()))
            self.assertIn('Kill switch: off', sb.cli(['doctor'])[1])

    def test_session_age_has_compact_units(self):
        with Sandbox() as sb:
            clock = FakeClock()
            codex_session(sb.codex, sb.project, [('Goal', 'Done')], at=clock.now())
            clock.sleep(3660)
            code, out, err = sb.cli(['sessions'], clock=clock)
            self.assertEqual(code, 0, err)
            self.assertIn('1h', out)
