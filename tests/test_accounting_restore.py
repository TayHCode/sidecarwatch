from tests.helpers import text_parts
import datetime as dt
import json
import stat
import unittest
from unittest.mock import patch
from sidecarwatch.ledger import DAY_CAP, LedgerError
from sidecarwatch.state import State
from sidecarwatch.codex_format import parse
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, codex_session, append_turn, nudge


def one_pass(sb, clock, delivery):
    return sb.cli(['watch', '--only', str(sb.project), '--allow', str(sb.project),
                   '--auto-reply', '--live', '--overnight'], clock=clock,
                  reviewer=FakeReviewer([nudge(nudge_type='continue')]), delivery=delivery)


class RestoreTests(unittest.TestCase):
    def test_audit_combined_restore_eight_passes_cannot_reset_caps(self):
        for restore_witness in (False, True):
            with self.subTest(restore_witness=restore_witness), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                state = State(sb.root/'state/sidecarwatch', clock)
                files = [state.send_log.path, state.send_log.anchor]
                if restore_witness:
                    files.append(state.send_log.witness)
                saved = {p: p.read_bytes() for p in files}
                path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], at=clock.now())
                delivery = FakeDelivery()
                self.assertEqual(one_pass(sb, clock, delivery)[0], 0)
                for _ in range(8):
                    for file, data in saved.items():
                        file.write_bytes(data)
                    (state.root/'state.json').unlink(missing_ok=True)
                    clock.sleep(661)
                    append_turn(path, 'Unlabelled peer update', 'All tests passed.', clock.now())
                    code, out, err = one_pass(sb, clock, delivery)
                    self.assertEqual(code, 10, err)
                    self.assertIn('send-log-unarmed', (state.root/'needs-you.md').read_text())
                self.assertEqual(len(delivery.calls), 1)
                self.assertFalse(state.send_log.witness.is_relative_to(state.root))
                self.assertEqual(stat.S_IMODE(state.send_log.witness.stat().st_mode), 0o600)

    def test_clock_jump_reboot_and_explicit_rearm_preserve_counts(self):
        for jump in ('forward', 'backward', 'reboot', 'slow-drift'):
            with self.subTest(jump=jump), Sandbox() as sb:
                clock = FakeClock(); sb.arm(clock)
                state = State(sb.root/'state/sidecarwatch', clock)
                path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
                session = parse(path, clock.now(), 60)
                for number in range(DAY_CAP):
                    session.id = str(number)
                    state.send_log.reserve(session, clock.now(), 50)
                if jump == 'forward': clock.value += dt.timedelta(seconds=86401)
                elif jump == 'backward': clock.value -= dt.timedelta(seconds=1)
                elif jump == 'reboot': clock.elapsed = 0
                else:
                    clock.sleep(10)
                    clock.value += dt.timedelta(seconds=4)
                    session.id = 'intermediate'
                    # A small forward skew must not age accounting faster.
                    self.assertEqual(len(state.send_log.attempts(clock.now())), DAY_CAP)
                    clock.value += dt.timedelta(seconds=4)
                with self.assertRaisesRegex(LedgerError, 'send-log-clock'):
                    state.send_log.reserve(session, clock.now(), 50)
                sb.arm(clock)
                self.assertEqual(len(state.send_log.attempts(clock.now())), DAY_CAP)
                session.id = 'new'
                with self.assertRaisesRegex(LedgerError, 'day-limit'):
                    state.send_log.reserve(session, clock.now(), 50)
                clock.sleep(86401)
                state.send_log.reserve(session, clock.now(), 50)
                self.assertEqual(len(state.send_log.attempts(clock.now())), DAY_CAP+1)

    def test_expiry_requires_both_elapsed_and_wall_time(self):
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            state = State(sb.root/'state/sidecarwatch', clock)
            path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
            session = parse(path, clock.now(), 60)
            for number in range(DAY_CAP):
                session.id = str(number); state.send_log.reserve(session, clock.now(), 50)
            clock.sleep(86398)
            clock.value += dt.timedelta(seconds=4)
            session.id = 'new'
            with self.assertRaisesRegex(LedgerError, 'day-limit'):
                state.send_log.reserve(session, clock.now(), 50)
            clock.sleep(3)
            state.send_log.reserve(session, clock.now(), 50)

    def test_partial_witness_write_missing_witness_and_lifetime_ceiling(self):
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            state = State(sb.root/'state/sidecarwatch', clock)
            path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
            session = parse(path, clock.now(), 60)
            for number in range(50):
                session.turns_finished = number+1
                state.send_log.reserve(session, clock.now(), 50)
            session.turns_finished += 1
            clock.value += dt.timedelta(seconds=100)
            sb.arm(clock)
            with self.assertRaisesRegex(LedgerError, 'max-turns'):
                state.send_log.reserve(session, clock.now(), 50)
            state.send_log.witness.unlink()
            with self.assertRaises(LedgerError): state.send_log.attempts(clock.now())
            sb.arm(clock)
            with patch(text_parts('sidecarwatch.ledger.write_pr', 'ivate'), side_effect=OSError('synthetic disk error')):
                with self.assertRaises(OSError): state.send_log.reserve(session, clock.now(), 50)
            with self.assertRaises(LedgerError): state.send_log.attempts(clock.now())
