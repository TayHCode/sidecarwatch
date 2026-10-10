from tests.helpers import text_parts
import json
import unittest
from tests.helpers import Sandbox, codex_session, claude_session


class DoctorTests(unittest.TestCase):
    def test_doctor_and_shapes(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[(text_parts('PR', 'IVATE_MESSAGE_MARKER'),'Done')],shape='B')
            claude_session(sb.claude,sb.project,[(text_parts('PR', 'IVATE_MESSAGE_MARKER'),'Done')])
            code,out,err=sb.cli(['doctor','--shapes','--json'])
            self.assertEqual(code,0,err)
            self.assertNotIn(text_parts('PR', 'IVATE_MESSAGE_MARKER'),out)
            value=json.loads(out)
            for agent in ('claude','codex'):
                self.assertTrue(value['clis'][agent]['found'])
                self.assertEqual(value['clis'][agent]['sessions'],1)
                self.assertIn('0.0.0 (fake)',value['clis'][agent]['version'])
            self.assertFalse(value['clis']['codex']['delivery'])
            self.assertFalse(value['clis']['claude']['delivery'])
            self.assertIn('event_msg/user_message',out)
