import json
import unittest
from sidecarwatch.codex_format import parse
from sidecarwatch.reviewer import answer, ReviewError
from tests.helpers import Sandbox, codex_session, FakeClock, FakeReviewer, FakeDelivery, nudge


class ParseLimitTests(unittest.TestCase):
    def test_nested_and_oversized_lines_do_not_crash_commands(self):
        for bad in ('['*100000+'0'+']'*100000, '{"type":"noise","value":"'+'x'*2000000+'"}'):
            with self.subTest(size=len(bad)), Sandbox() as sb:
                path = codex_session(sb.codex, sb.project, [('Goal', 'Work completed.')])
                with path.open('a') as f:
                    f.write(bad+'\n')
                session = parse(path, FakeClock().now(), 60)
                self.assertEqual(session.unknown_lines, 1)
                self.assertTrue(session.parse_limited)
                for args in (['sessions'], ['show','codex'], ['audit','codex','--list-only'], ['doctor','--shapes'], ['watch','--only',str(sb.project),'--list-only']):
                    code, out, err = sb.cli(args)
                    self.assertEqual(code, 8 if args[0]=='audit' else 0, err)
                sb.arm()
                delivery = FakeDelivery()
                code, out, err = sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live'], reviewer=FakeReviewer([nudge()]), delivery=delivery)
                self.assertEqual(code, 10, err)
                self.assertFalse(delivery.calls)
                self.assertIn('incomplete-transcript', out)

    def test_depth_bound_preserves_quoted_brackets_and_following_rows(self):
        with Sandbox() as sb:
            path = codex_session(sb.codex, sb.project, [('Goal','Literal '+'['*100)])
            session = parse(path, FakeClock().now(), 60)
            self.assertEqual(session.unknown_lines, 0)
            self.assertFalse(session.parse_limited)
            text = path.read_text()
            path.write_text('x'*2000000+'\n'+text)
            self.assertEqual(parse(path, FakeClock().now(), 60).turns_finished, 1)
        with self.assertRaises(ReviewError):
            answer('{"a":'+'['*100000+'0'+']'*100000+'}')
