import os
import unittest
from sidecarwatch.codex_format import parse
from tests.helpers import ROOT, FakeClock, Sandbox, codex_session


class CodexTests(unittest.TestCase):
    def test_codex_shape_a(self):
        s=parse(ROOT/'tests/fixtures/codex/shape_a.jsonl',FakeClock().now(),60)
        self.assertEqual((s.first_request,s.latest_instruction,s.final_message,s.turns_finished,s.state,s.cwd),('Add CSV export','Add CSV export','Export added.',1,'idle','/work/bookshelf-app'))

    def test_codex_shape_b(self):
        s=parse(ROOT/'tests/fixtures/codex/shape_b.jsonl',FakeClock().now(),60)
        self.assertEqual(s.first_request,'Add CSV export')
        self.assertEqual(s.final_message,'Export added.')
        self.assertEqual(len([m for m in s.messages if m[1]=='agent']),1)
        self.assertTrue(any(m[1]=='tool' and 'check export' in m[2] for m in s.messages))
        self.assertNotIn('TOOL_OUTPUT_ONLY',str(s.messages))

    def test_completion_summary_cannot_replace_actual_message(self):
        import json
        with Sandbox() as sb:
            p = codex_session(sb.codex, sb.project, [('Add CSV export','Earlier answer')], shape='B')
            rows = [json.loads(line) for line in p.read_text().splitlines()]
            rows[-1]['payload']['last_agent_message'] = 'Completion answer'
            p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            s = parse(p, FakeClock().now(), 60)
            self.assertEqual(s.final_message, 'Earlier answer')
            self.assertFalse(s.completion_confirmed)

    def test_codex_no_events_fallback(self):
        with Sandbox() as sb:
            p=sb.root/'rollout-sample.jsonl'
            p.write_text((ROOT/'tests/fixtures/codex/no_events.jsonl').read_text())
            clock=FakeClock()
            os.utime(p,(clock.now().timestamp(),)*2)
            self.assertEqual(parse(p,clock.now(),60).state,'running')
            clock.sleep(61)
            self.assertEqual(parse(p,clock.now(),60).state,'idle')

    def test_subagent_and_garbage(self):
        self.assertTrue(parse(ROOT/'tests/fixtures/codex/subagent.jsonl',FakeClock().now(),60).subagent)
        s=parse(ROOT/'tests/fixtures/codex/garbage.jsonl',FakeClock().now(),60)
        self.assertEqual(s.state,'unknown')
        self.assertEqual(s.unknown_lines,5)

    def test_shape_type_fuzz(self):
        import json
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[])
            with p.open('a') as f:
                for kind in [None, [], {}, 1, 'event_msg', 'response_item', 'session_meta']:
                    for value in [None, [], {}, 1, True, ['text',None,{'type':[]}]]:
                        f.write(json.dumps({'type':kind,'timestamp':value,'payload':{'type':value,'item':value,'content':value}})+'\n')
            parse(p,FakeClock().now(),60)
