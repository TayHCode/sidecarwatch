"""Round three failures, using generated sessions and fake reviewer processes."""
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sidecarwatch.scrub import ScrubUncertain, display_scrub, scrub
from tests.helpers import (ROOT, Sandbox, FakeClock, FakeReviewer, claude_session,
                           codex_session, text_parts, verdict)


class RoundThreeTests(unittest.TestCase):
    def test_folder_components_and_explicit_scope_outside_home(self):
        with Sandbox() as sb, tempfile.TemporaryDirectory(dir=ROOT/'.scratch') as work:
            project = Path(work)/'ordinary-folder'/'fixture-tests'
            project.mkdir(parents=True)
            claude_session(sb.claude, project, [('Add CSV export.', 'The CSV test passed.')])
            self.assertEqual(scrub(str(project), strict=True), str(project))
            self.assertIn(str(project), sb.cli(['sessions'])[1])
            for command in ('audit', 'show'):
                args = [command, '--only', str(project)] + (['--dry-run'] if command == 'audit' else [])
                code, out, err = sb.cli(args)
                self.assertEqual(code, 0, out + err)
                self.assertIn('CSV', out)
            claude_session(sb.claude, project, [('Check CSV.', 'The test passed.')], id='second-worker')
            code, out, err = sb.cli(['audit', '--only', str(project), '--dry-run'])
            self.assertEqual(code, 4, out + err)
            self.assertIn('ambiguous', err)
            self.assertIn('second-worker', err)
            for component in ('Qw9rT2yZ' * 5, 'deadbeef' * 5, text_parts('gh', 'p_', 'A' * 36)):
                with self.assertRaises(ScrubUncertain):
                    scrub(str(project/component), strict=True)

    def test_token_span_does_not_hide_agent_reply(self):
        value = text_parts('gh', 'p_', 'A' * 36)
        request = 'Use token ' + value + ' in the fixture.'
        final = 'Done. The fixture is updated and the tests pass in my check.'
        self.assertEqual(display_scrub(request), request.replace(value, '[REDACTED:github-token]'))
        for make, agent in ((claude_session, 'claude'), (codex_session, 'codex')):
            with self.subTest(agent=agent), Sandbox() as sb:
                make(getattr(sb, agent), sb.project, [(request, final)])
                for flags in ([], ['--json'], ['--last', '1']):
                    code, out, err = sb.cli(['show', agent, *flags])
                    self.assertEqual(code, 0, out + err)
                    self.assertNotIn(value, out)
                    self.assertIn(final, out)
                reviewer = FakeReviewer([])
                self.assertEqual(sb.cli(['audit', agent], reviewer=reviewer)[0], 8)
                self.assertFalse(reviewer.calls)

    def test_token_display_does_not_bypass_other_sensitive_values(self):
        value = text_parts('gh', 'p_', 'A' * 36)
        other = text_parts('Cedar', 'Brook79!')
        samples = [text_parts('https', '://user:', other, '@', 'host.invalid/path'),
                   text_parts('Pa', 'ssword\n', other),
                   text_parts('DB_PA', 'SSWORD=', other)]
        for sample in samples:
            with self.subTest(sample=sample):
                shown = display_scrub('Use token ' + value + ' in the fixture.\n' + sample)
                self.assertNotIn(value, shown)
                self.assertNotIn(other, shown)
        with Sandbox() as sb:
            claude_session(sb.claude, sb.project, [(text_parts('DB_PA', 'SSWORD'), other)])
            self.assertNotIn(other, sb.cli(['show', 'claude'])[1])

    def test_real_cli_audit_and_watch_share_persisted_needs(self):
        # Reproduce exit 10 with a WORKING/none/no answer whose low confidence
        # is promoted to Kind 2. The old audit printed an empty needs-you block
        # without recording it; open/status still reported the earlier two.
        with Sandbox() as sb:
            for index in range(3):
                claude_session(sb.claude, sb.project, [('Add CSV export.', 'The CSV test passed.')],
                               id='round-three-worker-' + str(index))
            def cli(*args):
                result = subprocess.run([sys.executable, '-B', '-m', 'sidecarwatch', *args],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                return result.returncode, result.stdout, result.stderr
            self.assertEqual(cli('init', '--allow-cross-vendor-review')[0], 0)
            (sb.fake/'verdict.json').write_text(json.dumps(verdict(kind='2', question_for_user='Which format?')))
            for index in range(2):
                self.assertEqual(cli('watch', '--session', 'round-three-worker-' + str(index))[0], 10)
            (sb.fake/'verdict.json').write_text(json.dumps(verdict(confidence='low', draft='')))
            code, out, err = cli('audit', 'round-three-worker-2')
            self.assertEqual(code, 10, out + err)
            self.assertIn('low-confidence', out)
            self.assertNotIn('Draft: NOT SENT', out)
            self.assertIn('3 open', cli('open')[1])
            self.assertEqual(json.loads(cli('status', '--json')[1])['need_you'], 3)
            self.assertIn('3 need you', cli('watch', '--session', 'round-three-worker-0')[1])
            (sb.fake/'verdict.json').write_text(json.dumps(verdict()))
            self.assertEqual(cli('audit', 'round-three-worker-2')[0], 10)
            self.assertEqual(json.loads(cli('status', '--json')[1])['need_you'], 2)
            for index in range(2):
                self.assertEqual(cli('resolve', 'round-three-worker-' + str(index))[0], 0)
            self.assertEqual(cli('audit', 'round-three-worker-2')[0], 0)
            self.assertEqual(cli('status')[0], 0)
            calls = [json.loads(line) for line in (sb.fake/'calls.jsonl').read_text().splitlines()]
            self.assertTrue(any(row['stdin'] for row in calls))
            self.assertTrue(all(row['program'] in ('claude', 'codex') for row in calls))

    def test_worker_question_overrides_reviewer_summary_in_both_commands(self):
        for command in ('audit', 'watch'):
            with self.subTest(command=command), Sandbox() as sb:
                question = 'Do you want me to push it now?'
                claude_session(sb.claude, sb.project, [('Add CSV export.', question)])
                fake = FakeReviewer([verdict(summary='The worker is on task.')])
                args = [command, 'claude'] if command == 'audit' else [command, '--only', str(sb.project)]
                code, out, err = sb.cli(args, reviewer=fake)
                self.assertEqual(code, 10, out + err)
                self.assertIn('worker-needs-human', out)
                opened = sb.cli(['open'])[1]
                self.assertIn('Question: ' + question, opened)
                self.assertNotIn('Question: The worker is on task.', opened)

    def test_future_timestamp_reason_and_privacy_exit_precedence(self):
        with Sandbox() as sb:
            clock = FakeClock()
            codex_session(sb.codex, sb.project, [('Add CSV export.', 'The test passed.')],
                          id='future-worker', at=clock.now() + dt.timedelta(hours=6))
            for flags in (['--dry-run'], []):
                code, out, err = sb.cli(['watch', '--session', 'future-worker', *flags], clock=clock)
                self.assertEqual(code, 0 if flags else 10, out + err)
                self.assertIn('invalid-timestamps', out + sb.cli(['open'])[1])
                self.assertNotIn('unsupported-turn-data', out)
            request = text_parts('gh', 'p_', 'B' * 36)
            claude_session(sb.claude, sb.project, [(request, 'Done.')], id='private-worker')
            code, out, err = sb.cli(['watch', '--session', 'private-worker', '--dry-run'])
            self.assertEqual(code, 8, out + err)
            self.assertIn('1 need you', out)
            self.assertEqual(json.loads(sb.cli(['status', '--json'])[1])['need_you'], 1)

    def test_enabled_consent_message_is_unambiguous(self):
        with Sandbox(consent=False) as sb:
            code, out, err = sb.cli(['init', '--allow-cross-vendor-review'])
            self.assertEqual(code, 0, out + err)
            self.assertIn('Cross-vendor review: ON', out)
            self.assertNotIn('until you enable', out)
            self.assertNotIn('to consent', out)
