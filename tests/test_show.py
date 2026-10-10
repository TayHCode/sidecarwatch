import json
import unittest
from tests.helpers import Sandbox, codex_session


class ShowTests(unittest.TestCase):
    def test_last_entries(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('First request','First answer'),('Latest instruction','Latest answer')])
            code,out,err=sb.cli(['show','codex','--last','1','--json'])
            self.assertEqual(code,0,err)
            self.assertEqual([row['text'] for row in json.loads(out)],['Latest answer'])
            self.assertEqual(sb.cli(['show','codex','--last','0'])[0],2)

    def test_show_scrubbed(self):
        with Sandbox() as sb:
            secret='sk'+'-'+'a'*24
            codex_session(sb.codex,sb.project,[('Add CSV export '+secret,'Export added.')],shape='B')
            code,out,err=sb.cli(['show','codex'])
            self.assertEqual(code,0)
            self.assertNotIn(secret,out)
            self.assertLess(out.index('user:'),out.index('agent:'))
            self.assertNotIn('TOOL_OUTPUT_ONLY',out)
            self.assertNotIn('shell:',out)
            self.assertIn('tool:',sb.cli(['show','codex','--tools'])[1])
            self.assertEqual(len(json.loads(sb.cli(['show','codex','--json'])[1])),2)
