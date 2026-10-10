from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile
from sidecarwatch.config import load
from sidecarwatch.profiles import load as load_profiles
from tests.helpers import Sandbox, ROOT


class UtilityTests(unittest.TestCase):
    def test_init_and_demo(self):
        with Sandbox() as sb:
            self.assertEqual(sb.cli(['init'])[0],0)
            cfg=sb.root/'config/sidecarwatch/config.toml'
            self.assertEqual(load(cfg)['auto_reply']['max_turns'],5)
            self.assertEqual(load_profiles(cfg.parent)[0]['name'],'bookshelf')
            original=cfg.read_text()
            self.assertIn('Skipped',sb.cli(['init'])[1])
            self.assertEqual(cfg.read_text(),original)
            before=set((ROOT/'.scratch').iterdir())
            with patch.object(tempfile,'tempdir',str(ROOT/'.scratch')):
                code,out,err=sb.cli(['demo'])
            self.assertEqual(code,0,err)
            self.assertTrue(out.startswith('DEMO'))
            self.assertTrue(out.splitlines()[-1].startswith('DEMO'))
            self.assertIn('What it is doing',out)
            self.assertFalse((sb.fake/'calls.jsonl').exists())
            self.assertEqual(set((ROOT/'.scratch').iterdir()),before)

    def test_all_command_help(self):
        with Sandbox() as sb:
            flags = {
                'sessions': '--agent --only --window --all',
                'show': '--since --chars --tools',
                'audit': '--reviewer --profile --only --since --list-only',
                'watch': '--only --session --agent --since --interval --for --until --max-passes --list-only --reviewer --auto-reply --no-auto-reply --allow --dry-run --live --overnight --max-turns',
                'stop': '', 'resume': '', 'rearm': '', 'doctor': '--shapes', 'init': '', 'demo': '',
            }
            for name, options in flags.items():
                code,out,err=sb.cli([name,'--help'])
                self.assertEqual(code,0)
                for option in (options + ' --config --claude-home --codex-home --json --version --help').split():
                    self.assertIn(option,out)
