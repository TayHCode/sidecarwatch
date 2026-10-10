from tests.helpers import text_parts
import json
import datetime as dt
import os
from pathlib import Path
import subprocess
import unittest

class ListingPortTests(unittest.TestCase):
    def test_match_and_pasteable_status(self):
        import json
        from tests.helpers import Sandbox, codex_session
        with Sandbox() as sb:
            sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
            codex_session(sb.codex,sb.project,[('Add export','The export is ready.')])
            (sb.codex/'session_index.jsonl').write_text(json.dumps({'id':sid,'thread_name':'Example worker 1'})+'\n')
            code,out,err=sb.cli(['sessions','--match','EXAMPLE','--long','--chars','10'])
            self.assertEqual(code,0,err)
            self.assertIn('Example worker 1 [IDLE',out)
            self.assertIn('~/bookshelf-app',out)
            self.assertIn('The export',out)
            self.assertNotIn('ready.',out)
            self.assertIn('No sessions',sb.cli(['sessions','--match','missing'])[1])
            self.assertFalse((sb.fake/'calls.jsonl').exists())
from sidecarwatch import sessions
from sidecarwatch.config import merge, DEFAULTS
from tests.helpers import Sandbox, claude_session, codex_session, FakeClock


class SessionTests(unittest.TestCase):
    def test_folder_scope_order_and_index_tolerance(self):
        with Sandbox() as sb:
            clock = FakeClock()
            claude_session(sb.claude, sb.project, [('Older goal', 'Done')], at=clock.now()-dt.timedelta(minutes=1))
            codex_session(sb.codex, sb.project, [('Newer goal', 'Done')], at=clock.now())
            outside = sb.root/'another-project'
            outside.mkdir()
            claude_session(sb.claude, outside, [('Other goal', 'Done')], id='bbbbbbbb-bbbb-4bbb-8bbb-aaaaaaaaaaa3')
            args = ['sessions', '--only', str(sb.project), '--json']
            rows = json.loads(sb.cli(args)[1])
            self.assertEqual([row['agent'] for row in rows], ['codex', 'claude'])
            self.assertEqual([row['name'] for row in rows], ['', ''])
            self.assertEqual(len(json.loads(sb.cli(args+['--agent','claude'])[1])), 1)
            index = sb.codex/'session_index.jsonl'
            index.write_text('bad\n[]\n')
            self.assertEqual(len(json.loads(sb.cli(args)[1])), 2)
            sid = rows[0]['id']
            with index.open('a') as stream:
                for name in ['first name', 'latest name']:
                    stream.write(json.dumps({'id': sid, 'thread_name': name})+'\n')
            self.assertEqual(json.loads(sb.cli(args)[1])[0]['name'], 'latest name')
            self.assertEqual(sb.cli(['show','latest name'])[0], 0)

    def test_self_sessions_never_reviewed(self):
        from tests.helpers import FakeReviewer, FakeDelivery
        with Sandbox() as sb:
            claude_session(sb.claude, sb.root/'state/sidecarwatch/reviewer-cwd', [('Goal','Done')])
            codex_session(sb.codex, sb.project, [('SIDECARWATCH-REVIEW-REQUEST v1','Done')])
            reviewer, delivery = FakeReviewer([]), FakeDelivery()
            code, out, err = sb.cli(['watch','--only',str(sb.root)], reviewer=reviewer, delivery=delivery)
            self.assertEqual(code, 0, err)
            self.assertEqual(reviewer.calls, [])
            self.assertEqual(delivery.calls, [])

    def test_excluded_files_and_window(self):
        with Sandbox() as sb:
            p=claude_session(sb.claude,sb.project,[('Goal','Done')])
            child=p.parent/p.stem/'subagents/agent-child.jsonl'
            child.parent.mkdir(parents=True)
            child.write_text(p.read_text())
            c=codex_session(sb.codex,sb.project,[('Goal','Done')],subagent=True)
            archive=sb.codex/'archived_sessions'/c.name
            archive.parent.mkdir(); archive.write_text(c.read_text())
            self.assertEqual(len(json.loads(sb.cli(['sessions','--json'])[1])),1)
            os.utime(p,(FakeClock().now().timestamp()-90000,)*2)
            self.assertEqual(json.loads(sb.cli(['sessions','--json'])[1]),[])
            self.assertEqual(len(json.loads(sb.cli(['sessions','--window','48h','--json'])[1])),1)

    def test_codex_names_from_index(self):
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[('Goal','Done')])
            (sb.codex/'session_index.jsonl').write_text('bad\n'+json.dumps({'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2','thread_name':'reading export'})+'\n')
            self.assertEqual(sb.cli(['show','reading export'])[0],0)
            self.assertIn('reading export',sb.cli(['sessions','--json'])[1])

    def test_only_folder_repo_profile(self):
        with Sandbox() as sb:
            subprocess.run(['git','init','-q',str(sb.project)],check=True)
            identity = {**os.environ, text_parts('GIT_AU', 'THOR_NAME'): 'Release Export',
                        text_parts('GIT_AU', 'THOR_EMAIL'): 'release-export', 'GIT_COMMITTER_NAME': 'Release Export',
                        'GIT_COMMITTER_EMAIL': 'release-export'}
            trailer = text_parts('Co-Au', 'thored-By: Claude Sonnet 5.5 <') + 'noreply' + '@' + 'anthropic.com>'
            subprocess.run(['git','-C',str(sb.project),'-c','commit.gpgsign=false','commit',
                            '--allow-empty','-qm','Initial','--trailer',trailer],env=identity,check=True)
            linked=sb.root/'linked'
            subprocess.run(['git','-C',str(sb.project),'worktree','add','-qb','linked',str(linked)],check=True)
            claude_session(sb.claude,linked,[('Goal','Done')])
            self.assertEqual(len(json.loads(sb.cli(['sessions','--only',str(sb.project),'--json'])[1])),1)
            self.assertEqual(sb.cli(['sessions','--only','missing-folder'])[0],2)
            self.assertEqual(sb.cli(['show','aaaaaa'])[0],0)

    def test_sessions_output_and_self_skipped(self):
        with Sandbox() as sb:
            claude_session(sb.claude,sb.project,[('Add CSV export','Done')])
            code,out,err=sb.cli(['sessions'])
            self.assertEqual(code,0)
            self.assertIn('agent   id',out)
            self.assertIn('~/bookshelf-app',out)
            data=json.loads(sb.cli(['--json','sessions'])[1])
            self.assertNotIn('first_request',data[0])
            self.assertNotIn('final_message',data[0])
            codex_session(sb.codex,sb.root/'state/sidecarwatch/reviewer-cwd',[('Goal','Done')])
            self.assertEqual(len(json.loads(sb.cli(['sessions','--json'])[1])),1)
            codex_session(sb.codex,sb.project,[('SIDECARWATCH-REVIEW-REQUEST v1','Done')])
            self.assertEqual(len(json.loads(sb.cli(['sessions','--json'])[1])),1)
