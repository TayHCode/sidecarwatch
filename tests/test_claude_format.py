import datetime as dt
import json
import os
import unittest
from sidecarwatch.claude_format import parse
from tests.helpers import Sandbox, claude_session, FakeClock, ROOT


class ClaudeTests(unittest.TestCase):
    def test_claude_basic(self):
        with Sandbox() as sb:
            p=claude_session(sb.claude,sb.project,[('Add CSV export','Added.'),('Test export','Tests passed.')])
            s=parse(p,FakeClock().now(),60)
            self.assertEqual((s.first_request,s.latest_instruction,s.final_message,s.turns_finished,s.state,s.cwd),('Add CSV export','Test export','Tests passed.',2,'idle',str(sb.project)))

    def test_claude_running(self):
        s=parse(ROOT/'tests/fixtures/claude/running.jsonl',FakeClock().now(),60)
        self.assertEqual(s.state,'running')
        self.assertEqual(s.final_message,'')

    def test_claude_noise_is_skipped(self):
        s=parse(ROOT/'tests/fixtures/claude/noise.jsonl',FakeClock().now(),60)
        self.assertEqual(s.first_request,'')
        self.assertEqual(s.unknown_lines,3)

    def test_claude_quiet_fallback(self):
        with Sandbox() as sb:
            clock=FakeClock()
            p=claude_session(sb.claude,sb.project,[('Add CSV export','Added.')],stop_reason=None)
            os.utime(p,(clock.now().timestamp(),)*2)
            self.assertEqual(parse(p,clock.now(),60).state,'running')
            clock.sleep(61)
            self.assertEqual(parse(p,clock.now(),60).state,'idle')

    def test_wrong_types_and_late_result(self):
        with Sandbox() as sb:
            p=claude_session(sb.claude,sb.project,[('Add CSV export','Added.')])
            with p.open('a') as f:
                for row in [{'type':'user','message':{'content':[{'type':'tool_result','content':'hidden'}]}},{'type':'assistant','message':[]},{'type':'user','message':{'content':[4,{}]}}]:
                    f.write(json.dumps(row)+'\n')
            self.assertEqual(parse(p,FakeClock().now(),60).state,'running')

    def test_shape_type_fuzz(self):
        with Sandbox() as sb:
            p=claude_session(sb.claude,sb.project,[])
            with p.open('a') as f:
                for kind in [None, [], {}, 1, 'user', 'assistant', 'system']:
                    for value in [None, [], {}, 1, True, ['text',None,{'type':[]}]]:
                        f.write(json.dumps({'type':kind,'cwd':value,'timestamp':value,'message':{'content':value}})+'\n')
            parse(p,FakeClock().now(),60)
