import json
import unittest
from sidecarwatch.codex_format import parse
from sidecarwatch.policy import label, TEMPLATES
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, codex_session, append_turn, nudge


class MarkerTests(unittest.TestCase):
    def test_duplicate_representations_allow_five_actual_attempts(self):
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')], shape='B', at=clock.now())
            finals = iter(['All tests passed.', 'Verification succeeded.', 'Done.', 'Completed successfully.', 'Tests passed.'])
            def respond(session, text):
                append_turn(path, text, next(finals), clock.now())
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                user = len(rows)-3
                rows.insert(user+1, {'type': 'response_item', 'timestamp': rows[user]['timestamp'],
                                    'payload': {'type': 'message', 'role': 'user',
                                                'content': [{'type': 'input_text', 'text': text}]}})
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            delivery = FakeDelivery(respond)
            for _ in range(6):
                # The journal, not the disposable cache, must retain attempts.
                (sb.root/'state/sidecarwatch/state.json').unlink(missing_ok=True)
                code, out, err = sb.cli(['watch', '--only', str(sb.project), '--allow', str(sb.project),
                                        '--auto-reply', '--live', '--overnight'], clock=clock,
                                       reviewer=FakeReviewer([nudge(nudge_type='continue')]), delivery=delivery)
                clock.sleep(661)
            self.assertEqual(len(delivery.calls), 5)
            self.assertEqual(code, 10, err)
            self.assertIn('max-turns', out)
            session = parse(path, clock.now(), 60)
            self.assertEqual((session.auto_turns, session.auto_total, len(session.auto_stamps)), (5, 5, 5))
            for count, (_, text) in enumerate(delivery.calls, 1):
                self.assertIn(f'reply {count}/5', text)

    def test_repeated_logical_messages_count_and_escaped_aliases_deduplicate(self):
        with Sandbox() as sb:
            clock = FakeClock()
            path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], shape='B', at=clock.now())
            session = parse(path, clock.now(), 60)
            frame = label(session, TEMPLATES['continue'], 'claude', 5)
            for _ in range(2):
                append_turn(path, frame, 'Work completed.', clock.now())
                clock.sleep(1)
                row = {'type': 'response_item', 'timestamp': clock.now().isoformat(),
                       'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': frame}]}}
                with path.open('a') as stream:
                    stream.write(json.dumps(row).replace('[AUTOMATED', r'\u005bAUTOMATED')+'\n')
            parsed = parse(path, clock.now(), 60)
            self.assertEqual((parsed.auto_turns, parsed.auto_total), (2, 2))
            self.assertEqual(len(parsed.auto_stamps), 2)
            # Same counter, different body is a distinct logical message.
            other = frame.replace(TEMPLATES['continue'], TEMPLATES['summarize'])
            append_turn(path, other, 'Done.', clock.now())
            self.assertEqual(parse(path, clock.now(), 60).auto_total, 3)

    def test_wrapped_non_user_aliases_and_multiple_fields_count_once(self):
        with Sandbox() as sb:
            clock = FakeClock()
            path = codex_session(sb.codex, sb.project, [('Goal', 'Done.')], at=clock.now())
            session = parse(path, clock.now(), 60)
            frame = label(session, TEMPLATES['continue'], 'claude', 5)
            append_turn(path, '\nwrapper\n'+frame, 'Work completed.', clock.now())
            with path.open('a') as stream:
                stream.write(json.dumps({'type': 'peer_note', 'payload': {'message': frame, 'copy': frame}})+'\n')
            self.assertEqual(parse(path, clock.now(), 60).auto_total, 1)
