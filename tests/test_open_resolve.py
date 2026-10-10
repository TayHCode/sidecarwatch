import json
import unittest
from tests.helpers import Sandbox, FakeReviewer, FakeDelivery, nudge, verdict, codex_session, append_turn

class OpenTests(unittest.TestCase):
    def test_current_list_since_and_resolution(self):
        with Sandbox() as sb:
            sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
            p=codex_session(sb.codex,sb.project,[('Add export','Done.')])
            (sb.codex/'session_index.jsonl').write_text(json.dumps({'id':sid,'thread_name':'Example worker 1'})+'\n')
            args=['watch','--only',str(sb.project)]
            value=nudge(kind='2',question_for_user='Which format?',draft='Choose the required format.')
            delivery=FakeDelivery()
            self.assertEqual(sb.cli(args,reviewer=FakeReviewer([value]),delivery=delivery)[0],10)
            state=sb.root/'state/sidecarwatch/state.json'
            before=json.loads(state.read_text())['needs']['codex:'+sid]['since']
            append_turn(p,'Same task','Tests passed.')
            self.assertEqual(sb.cli(args,reviewer=FakeReviewer([value]))[0],10)
            self.assertEqual(json.loads(state.read_text())['needs']['codex:'+sid]['since'],before)
            code,out,err=sb.cli(['open']); self.assertEqual(code,10,err)
            self.assertIn('Which format?',out); self.assertIn('Choose the required format.',out)
            self.assertEqual(sb.cli(['resolve','Example worker 1'])[0],0)
            self.assertIn('0 open',sb.cli(['open'])[1])
            self.assertEqual(delivery.calls,[])

    def test_none_clears_current_item(self):
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[('Goal','Done.')])
            args=['watch','--only',str(sb.project)]
            sb.cli(args,reviewer=FakeReviewer([verdict(kind='2')]))
            append_turn(p,'Stay on task','Tests passed.')
            self.assertEqual(sb.cli(args,reviewer=FakeReviewer([verdict()]))[0],0)
            self.assertIn('0 open',sb.cli(['open'])[1])
