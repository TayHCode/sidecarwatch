import unittest
from tests.helpers import Sandbox, FakeClock, FakeDelivery, FakeReviewer, codex_session, append_turn, nudge


class StallTests(unittest.TestCase):
    def test_overnight_limits_report_pause_and_nonzero_exit(self):
        for interval, expected, reason in [('60s', 1, 'cooldown'), ('5m', 6, 'rate-limit')]:
            with self.subTest(interval=interval), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], shape='B', at=clock.now())
                answers = iter(['Tests passed.', 'Verification succeeded.', 'Completed successfully.', 'All tests passed.', 'Task completed successfully.', 'Done.'])
                delivery = FakeDelivery(lambda s, text: append_turn(path, text, next(answers), clock.now()))
                args = ['watch', '--only', str(sb.project), '--auto-reply', '--allow', str(sb.project), '--live', '--overnight', '--max-turns', '20', '--interval', interval, '--for', '8h']
                code, out, err = sb.cli(args, clock=clock, reviewer=FakeReviewer([nudge(nudge_type='continue')]*7), delivery=delivery)
                self.assertEqual(len(delivery.calls), expected)
                self.assertEqual(code, 10, err)
                self.assertIn('Automatic replies paused for this session: '+reason, out)
                self.assertIn(reason, (sb.root/'state/sidecarwatch/needs-you.md').read_text())

    def test_dry_run_nudge_draft_printed_once_without_needs_entry(self):
        with Sandbox() as sb:
            codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')])
            code, out, err = sb.cli(['watch', '--only', str(sb.project), '--auto-reply', '--allow', str(sb.project)], reviewer=FakeReviewer([nudge(should_correct='yes')]), delivery=FakeDelivery())
            self.assertEqual(code, 0, err)
            self.assertEqual(out.count("Re-read the user's stated goal"), 1)
            self.assertNotIn('Needs you', out)

    def test_eight_hour_review_budget_persists_morning_status(self):
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], shape='B', at=clock.now())
            delivery = FakeDelivery(lambda session, text: append_turn(path, text, 'All tests passed.', clock.now()))
            cfg = sb.root/'cfg.toml'; cfg.write_text('[reviewer]\nmax_per_run=1\n')
            args = ['watch', '--only', str(sb.project), '--allow', str(sb.project), '--auto-reply', '--live',
                    '--overnight', '--interval', '11m', '--for', '8h', '--config', str(cfg)]
            reviewer = FakeReviewer([nudge(nudge_type='continue')])
            code, out, err = sb.cli(args, reviewer=reviewer, delivery=delivery, clock=clock)
            self.assertEqual(code, 10, err)
            self.assertEqual((len(delivery.calls), len(reviewer.calls)), (1, 1))
            root = sb.root/'state/sidecarwatch'
            self.assertIn('review-budget', (root/'needs-you.md').read_text())
            self.assertEqual((root/'log.jsonl').read_text().count('review-budget'), 1)
            code, _, _ = sb.cli(['watch', '--only', str(sb.project)], reviewer=FakeReviewer([]), clock=clock)
            self.assertEqual(code, 10)
            self.assertIn('review-budget', (root/'needs-you.md').read_text())

    def test_failed_unavailable_stale_and_interrupted_delivery_visible(self):
        from sidecarwatch.state import Stopped
        for outcome in ('failed', 'unavailable', 'stale', 'interrupted'):
            with self.subTest(outcome=outcome), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
                class Delivery(FakeDelivery):
                    def available(self, session):
                        if outcome == 'stale': append_turn(path, 'Wait for me', 'Done.', clock.now())
                        return outcome != 'unavailable'
                    def send(self, session, text):
                        super().send(session, text)
                        if outcome == 'interrupted': raise Stopped('stopped')
                        return False
                delivery = Delivery()
                code, out, err = sb.cli(['watch', '--only', str(sb.project), '--allow', str(sb.project),
                                        '--auto-reply', '--live', '--overnight'], clock=clock,
                                       reviewer=FakeReviewer([nudge(nudge_type='continue')]), delivery=delivery)
                self.assertEqual(code, {'failed': 5, 'unavailable': 10, 'stale': 10, 'interrupted': 3}[outcome], err)
                root = sb.root/'state/sidecarwatch'
                reason = {'failed': 'send-failed', 'unavailable': 'no-injection', 'stale': 'stale', 'interrupted': 'interrupted'}[outcome]
                self.assertIn(reason, (root/'needs-you.md').read_text())
                if outcome == 'failed':
                    self.assertEqual(sb.cli(['watch', '--only', str(sb.project)], clock=clock, reviewer=FakeReviewer([]))[0], 10)
                    self.assertEqual(len(delivery.calls), 1)

    def test_later_successful_review_clears_resolved_pause(self):
        from tests.helpers import verdict
        with Sandbox() as sb:
            clock = FakeClock()
            path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
            args = ['watch', '--only', str(sb.project)]
            self.assertEqual(sb.cli(args, clock=clock, reviewer=FakeReviewer([verdict(reply_kind='needs_user')]))[0], 10)
            append_turn(path, 'Resolved; finish the task', 'Completed successfully.', clock.now())
            self.assertEqual(sb.cli(args, clock=clock, reviewer=FakeReviewer([verdict(status='done')]))[0], 0)
            self.assertIn('0 open',(sb.root/'state/sidecarwatch/needs-you.md').read_text())
