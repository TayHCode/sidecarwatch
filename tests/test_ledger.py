import json
import shutil
import unittest
from sidecarwatch.ledger import LedgerError, DAY_CAP
from sidecarwatch.state import State, Stopped
from sidecarwatch.codex_format import parse
from sidecarwatch.policy import label, TEMPLATES
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, nudge, codex_session, append_turn


class LedgerTests(unittest.TestCase):
    def run_pass(self, sb, clock, delivery, **changes):
        args = ['watch', '--only', str(sb.project), '--allow', str(sb.project), '--auto-reply', '--live', '--overnight']
        return sb.cli(args, clock=clock, reviewer=FakeReviewer([nudge(nudge_type='continue', **changes)]), delivery=delivery)

    def test_absent_log_requires_explicit_command(self):
        with Sandbox() as sb:
            clock = FakeClock()
            path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
            delivery = FakeDelivery()
            code, out, err = self.run_pass(sb, clock, delivery)
            self.assertEqual(code, 10, err)
            self.assertIn('send-log-unarmed', out)
            self.assertEqual(delivery.calls, [])
            sb.arm(clock)
            append_turn(path, 'Next check', 'Tests passed.', clock.now())
            self.assertEqual(self.run_pass(sb, clock, delivery)[0], 0)
            self.assertEqual(len(delivery.calls), 1)

    def test_wrapped_missing_and_non_user_echoes_cap_across_runs(self):
        for echo in ('absent', 'wrapped', 'non-user'):
            with self.subTest(echo=echo), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], shape='B', at=clock.now())
                answers = iter(['Tests passed.', 'Verification succeeded.', 'All tests passed.', 'Task completed successfully.', 'Done.'])
                def respond(session, text):
                    clock.sleep(601)
                    append_turn(path, '\nwrapper\n' + text if echo == 'wrapped' else 'Unlabelled input', next(answers), clock.now())
                    if echo == 'non-user':
                        with path.open('a') as f:
                            f.write(json.dumps({'type': 'unknown', 'payload': {'message': text}})+'\n')
                delivery = FakeDelivery(respond)
                for _ in range(6):
                    # Disposable state loss cannot erase the durable accounting.
                    (sb.root/'state/sidecarwatch/state.json').unlink(missing_ok=True)
                    code, out, err = self.run_pass(sb, clock, delivery)
                expected = 1 if echo == 'non-user' else 5
                self.assertEqual(len(delivery.calls), expected)
                self.assertEqual(code, 10, err)
                self.assertIn('invalid-timestamps' if echo == 'non-user' else 'max-turns', out)
                for count, (_, text) in enumerate(delivery.calls, 1):
                    self.assertIn(f'reply {count}/5', text)
                sb.arm(clock)  # Intact re-arm cannot evade the cap.
                self.assertEqual(self.run_pass(sb, clock, delivery)[0], 10)  # unresolved pause remains visible
                self.assertEqual(len(delivery.calls), expected)

    def test_wiped_corrupt_missing_or_rolled_back_log_blocks(self):
        for damage in ('folder', 'missing', 'empty', 'corrupt', 'truncate', 'anchor'):
            with self.subTest(damage=damage), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
                delivery = FakeDelivery()
                self.assertEqual(self.run_pass(sb, clock, delivery)[0], 0)
                root = sb.root/'state/sidecarwatch'; log = root/'sends.jsonl'
                if damage == 'folder': shutil.rmtree(root)
                elif damage == 'missing': log.unlink()
                elif damage == 'empty': log.write_text('')
                elif damage == 'corrupt': log.write_text('broken\n')
                elif damage == 'truncate': log.write_text(log.read_text().splitlines()[0]+'\n')
                else: (root/'send-anchor.json').unlink()
                clock.sleep(601)
                append_turn(path, 'Another test', 'Tests passed.', clock.now())
                code, out, err = self.run_pass(sb, clock, delivery)
                self.assertEqual(code, 10, err)
                self.assertIn('send-log-unarmed', out)
                self.assertEqual(len(delivery.calls), 1)
                sb.cli(['resume'])
                state = State(root, clock)
                with self.assertRaises(LedgerError): state.send_log.read()
                sb.arm(clock)
                self.assertTrue(State(root, clock).send_log.read())

    def test_unchanged_transcript_and_interrupted_attempt_not_retried(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
                def stop(session, text):
                    if interrupted:
                        sb.cli(['stop'])
                        raise Stopped('stopped')
                delivery = FakeDelivery(stop)
                self.run_pass(sb, clock, delivery)
                sb.cli(['resume'])
                (sb.root/'state/sidecarwatch/state.json').unlink()
                clock.sleep(601)
                code, out, err = self.run_pass(sb, clock, delivery)
                self.assertEqual(code, 10, err)
                self.assertIn('already-attempted', out)
                self.assertEqual(len(delivery.calls), 1)

    def test_global_daily_cap_and_expiry_with_new_sessions(self):
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
            session = parse(path, clock.now(), 60)
            state = State(sb.root/'state/sidecarwatch', clock)
            for number in range(DAY_CAP):
                session.id = str(number)
                state.send_log.reserve(session, clock.now(), 50)
            session.id = 'another'
            with self.assertRaisesRegex(LedgerError, 'day-limit'):
                state.send_log.reserve(session, clock.now(), 50)
            delivery = FakeDelivery()
            code, out, err = self.run_pass(sb, clock, delivery)
            self.assertEqual(code, 10, err)
            self.assertIn('day-limit', out)
            self.assertFalse(delivery.calls)
            clock.sleep(86401)
            state.send_log.reserve(session, clock.now(), 50)

    def test_non_user_transcript_marker_is_counted(self):
        with Sandbox() as sb:
            clock = FakeClock()
            path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
            session = parse(path, clock.now(), 60)
            text = label(session, TEMPLATES['continue'], 'claude', 5)
            with path.open('a') as f:
                f.write(json.dumps({'type': 'unknown', 'payload': text})+'\n')
            self.assertEqual(parse(path, clock.now(), 60).auto_total, 1)

    def test_every_corrupt_json_shape_has_visible_stop_and_explicit_recovery(self):
        shapes = [b'null\n', b'"text"\n', b'true\n', b'false\n', b'0\n', b'1.5\n',
                  b'[]\n', b'{}\n', b'NaN\n', b'Infinity\n', b'{bad\n', b'\xff\n',
                  b'[' * 100 + b']' * 100 + b'\n', b' ' * 17000 + b'\n',
                  b'{"hash":"a","hash":"b"}\n', b'{"hash":[]}\n', b'']
        for filename in ('sends.jsonl', 'send-anchor.json'):
            for raw in shapes:
                with self.subTest(filename=filename, shape=shapes.index(raw)), Sandbox() as sb:
                    clock = FakeClock(); sb.arm(clock)
                    codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
                    root = sb.root/'state/sidecarwatch'
                    (root/filename).write_bytes(raw)
                    before = {name: (root/name).read_bytes() for name in ('sends.jsonl', 'send-anchor.json')}
                    delivery = FakeDelivery()
                    code, out, err = self.run_pass(sb, clock, delivery)
                    self.assertEqual(code, 10, err)
                    self.assertFalse(delivery.calls)
                    self.assertIn('send-log-unarmed', (root/'needs-you.md').read_text())
                    self.assertEqual(sb.cli(['rearm'], clock=clock)[0], 0)
                    for name, data in before.items():
                        saved = list(root.glob(name + '.saved-*'))
                        self.assertEqual(len(saved), 1)
                        self.assertEqual(saved[0].read_bytes(), data)
                    self.assertTrue(State(root, clock).send_log.read())
