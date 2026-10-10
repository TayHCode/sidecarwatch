from tests.helpers import text_parts
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from sidecarwatch.sessions import folders, matches, Session
from tests.helpers import Sandbox, FakeReviewer, FakeDelivery, nudge, codex_session


class AllowSafetyTests(unittest.TestCase):
    def test_empty_allow_scope_and_profile_reject(self):
        with Sandbox() as sb:
            codex_session(sb.codex, sb.project, [('Goal', 'Done')])
            delivery = FakeDelivery(); reviewer = FakeReviewer([])
            for empty in ('', ' ', os.environ.get('UNSET_PROJECT_FOR_TEST', '')):
                args = ['watch', '--only', str(sb.project), '--auto-reply', '--allow', empty, '--live']
                self.assertEqual(sb.cli(args, reviewer=reviewer, delivery=delivery)[0], 2)
                self.assertEqual(sb.cli(['sessions', '--only', empty])[0], 2)
                profile = sb.root/'config/sidecarwatch/profiles/empty.toml'
                profile.parent.mkdir(parents=True, exist_ok=True)
                profile.write_text('name="empty"\npaths=["'+empty+'"]\n[auto_reply]\nallow=true\n')
                self.assertEqual(sb.cli(['sessions'])[0], 2)
                profile.unlink()
            self.assertFalse(delivery.calls)
            self.assertFalse(reviewer.calls)
            self.assertFalse(matches(Session('', '', Path('.'), folders={str(sb.project)}), ''))

    def test_git_environment_cannot_reassign_project(self):
        with Sandbox() as sb:
            subprocess.run(['git', 'init', '-q', str(sb.project)], check=True)
            outside = sb.root/'outside'; outside.mkdir()
            subprocess.run(['git', 'init', '-q', str(outside)], check=True)
            cases = [{'GIT_DIR': str(sb.project/'.git')}, {'GIT_COMMON_DIR': str(sb.project/'.git')},
                     {'GIT_WORK_TREE': str(sb.project), 'GIT_DIR': str(sb.project/'.git')},
                     {'GIT_CONFIG_COUNT': '1', text_parts('GIT_CONFIG_KE', 'Y_0'): 'core.worktree', 'GIT_CONFIG_VALUE_0': str(sb.project)}]
            for env in cases:
                with patch.dict(os.environ, env):
                    result = folders(str(outside), {})
                    self.assertEqual(result, {str(outside)})
                    s = Session('', '', Path('.'), folders=result)
                    self.assertFalse(matches(s, str(sb.project)))

    def test_relative_transcript_paths_and_symlink_escape(self):
        with Sandbox() as sb:
            sb.arm()
            for cwd in ('.', '../bookshelf-app', '~/bookshelf-app', '', ' '):
                self.assertEqual(folders(cwd, {}), set())
                codex_session(sb.codex, cwd, [('Goal', 'Done')])
                d = FakeDelivery()
                code, out, err = sb.cli(['watch', '--session', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', '--auto-reply', '--allow', str(sb.project), '--live'], reviewer=FakeReviewer([nudge()]), delivery=d)
                self.assertFalse(d.calls)
            outside = sb.root/'outside'; outside.mkdir()
            link = sb.project/'escape'; link.symlink_to(outside, target_is_directory=True)
            sibling = sb.root/'bookshelf-app-extra'; sibling.mkdir()
            for cwd in (link, sb.project/'..'/'outside', sibling):
                s = Session('', '', Path('.'), folders=folders(str(cwd), {}))
                self.assertFalse(matches(s, sb.project))
            loop = sb.project/'loop'; loop.symlink_to(loop)
            self.assertEqual(folders(str(loop), {}), set())
            child = sb.project/'child'; child.mkdir()
            self.assertTrue(matches(Session('', '', Path('.'), folders=folders(str(child), {})), sb.project))
