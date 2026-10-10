"""Targeted round four regressions with synthetic data and fake reviewers."""
import datetime as dt
import json
import re
import unittest

from sidecarwatch.reviewer import InvalidVerdict, SCHEMA, validate
from tests.helpers import ROOT, Sandbox, FakeReviewer, claude_session, codex_session, text_parts, verdict


def documented_reply():
    text = (ROOT / 'docs/event-shapes.md').read_text()
    section = text.split('## Reviewer reply schema', 1)[1]
    return json.loads(re.search(r'```json\n(.*?)\n```', section, re.S)[1])


def needs_snapshot(sb):
    root = sb.root / 'state/sidecarwatch'
    data = json.loads((root / 'state.json').read_text()) if (root / 'state.json').exists() else {}
    files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.glob('needs-you*')}
    return data.get('needs', {}), data.get('pauses', {}), files


class RoundFourTests(unittest.TestCase):
    def test_dry_run_keeps_needs_state_and_files_unchanged(self):
        for existing in (False, True):
            with self.subTest(existing=existing), Sandbox() as sb:
                if existing:
                    claude_session(sb.claude, sb.project, [('Add CSV.', 'Done.')], id='prior-worker')
                    sb.cli(['audit', 'prior-worker'], reviewer=FakeReviewer([verdict(kind='2')]))
                codex_session(sb.codex, sb.project, [('Add CSV.', 'Done.')], id='future-worker',
                              at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=6))
                claude_session(sb.claude, sb.project, [('Add CSV.', 'Which format?')], id='question-worker')
                claude_session(sb.claude, sb.project, [(text_parts('gh', 'p_', 'A' * 36), 'Done.')], id='private-worker')
                for sid in ('future-worker', 'question-worker', 'private-worker'):
                    for flags in ([], ['--json']):
                        before = needs_snapshot(sb)
                        code, out, err = sb.cli(['watch', '--session', sid, '--dry-run', *flags])
                        self.assertEqual(code, 8 if sid == 'private-worker' else 10 if existing else 0, out + err)
                        self.assertEqual(needs_snapshot(sb), before)
                        self.assertEqual(sb.cli(['status'])[0], 10 if existing else 0)
                        self.assertEqual(sb.cli(['open'])[0], 10 if existing else 0)
                self.assertFalse((sb.fake / 'calls.jsonl').exists())

    def test_documented_reply_validates_and_names_every_field(self):
        example = documented_reply()
        self.assertEqual(dict(validate(example)), example)
        self.assertEqual(set(example), set(SCHEMA['required']))
        for value, expected in (({}, SCHEMA['required']), (dict(example, checks={}), SCHEMA['properties']['checks']['required'])):
            with self.assertRaises(InvalidVerdict) as raised:
                validate(value)
            for key in expected:
                self.assertIn(key, str(raised.exception))
            self.assertIn('docs/event-shapes.md', str(raised.exception))

    def test_completed_review_using_documented_fake_cli_reply(self):
        with Sandbox() as sb:
            claude_session(sb.claude, sb.project, [('Add CSV.', 'The CSV test passed.')], id='clean-worker')
            sb.cli(['init', '--allow-cross-vendor-review'])
            example = documented_reply()
            (sb.fake / 'verdict.json').write_text(json.dumps(dict(example, confidence='low', draft='')))
            self.assertEqual(sb.cli(['watch', '--session', 'clean-worker'])[0], 10)
            self.assertIn('1 open', sb.cli(['open'])[1])
            self.assertEqual(json.loads(sb.cli(['status', '--json'])[1])['need_you'], 1)
            (sb.fake / 'verdict.json').write_text(json.dumps(example))
            code, out, err = sb.cli(['audit', 'clean-worker'])
            self.assertEqual(code, 0, out + err)
            self.assertIn('Label: WORKING', out)
            for args in (['watch', '--session', 'clean-worker'], ['open'], ['status']):
                self.assertEqual(sb.cli(args)[0], 0)
            self.assertEqual(json.loads(sb.cli(['status', '--json'])[1])['need_you'], 0)
            calls = [json.loads(line) for line in (sb.fake / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(sum(bool(row['stdin']) for row in calls), 2)

    def test_unreviewable_question_is_stored_and_scrubbed(self):
        for logging in ('verdict', 'none'):
            with self.subTest(logging=logging), Sandbox() as sb:
                question = 'Do you want me to push it now?'
                claude_session(sb.claude, sb.project, [('Add CSV.', question)], id='question-worker')
                cfg = sb.root / 'config.toml'
                cfg.write_text('[log]\ntext = "' + logging + '"\n')
                args = ['watch', '--session', 'question-worker', '--config', str(cfg)]
                self.assertEqual(sb.cli(args + ['--dry-run'])[0], 0)
                self.assertEqual(sb.cli(args)[0], 10)
                opened = sb.cli(['open'])[1]
                self.assertIn('worker-needs-human', opened)
                self.assertIn('Question: ' + question, opened) if logging != 'none' else self.assertNotIn(question, opened)
                self.assertFalse((sb.fake / 'calls.jsonl').exists())

    def test_consent_text_discloses_residual_personal_values(self):
        for args in (['init', '--allow-cross-vendor-review'], ['audit', 'claude'],
                     ['watch', '--session', 'question-worker'], ['audit', 'claude', '--allow-cross-vendor-review']):
            with self.subTest(args=args), Sandbox(consent=False) as sb:
                claude_session(sb.claude, sb.project, [('Add CSV.', 'Done.')], id='question-worker')
                _, out, err = sb.cli(args, reviewer=FakeReviewer([verdict()]))
                self.assertIn('Phone numbers and unseparated SSN-style IDs may remain raw', out + err)

    def test_sessions_display_known_ids_without_card_false_positive(self):
        with Sandbox() as sb:
            sid = text_parts('16-15-50-', '29088627-8676-4a5c-8ff1-ab73cb87185c')
            codex_session(sb.codex, sb.project, [('Add CSV.', 'Done.')], id=sid)
            for flags in ([], ['--json'], ['--long']):
                code, out, err = sb.cli(['sessions', *flags])
                self.assertEqual(code, 0, out + err)
                self.assertIn(sid, out)
                self.assertNotIn('[REDACTED:card]', out)
