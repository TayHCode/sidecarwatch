"""Synthetic regressions for consent, format metadata and useful local output."""
import json
import unittest
from unittest.mock import patch

from sidecarwatch import config
from sidecarwatch.claude_format import parse as claude_parse
from sidecarwatch.codex_format import parse as codex_parse
from sidecarwatch.reviewer import ClaudeReviewer, ReviewError
from sidecarwatch.state import State
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, claude_session, codex_session, verdict, write_lines, text_parts


class RoundOneTests(unittest.TestCase):
    def test_default_gate_calls_no_reviewer_process_or_network(self):
        self.assertFalse(config.merge(config.DEFAULTS, {})['privacy']['allow_cross_vendor_review'])
        for agent, make, other, vendor in [('claude', claude_session, 'codex', 'OpenAI'), ('codex', codex_session, 'claude', 'Anthropic')]:
            with self.subTest(agent=agent), Sandbox(consent=False) as sb:
                make(getattr(sb, agent), sb.project, [('Add CSV export.', 'The CSV test passed.')])
                reviewer, delivery = FakeReviewer([]), FakeDelivery()
                with patch('sidecarwatch.reviewer.run', side_effect=AssertionError('CLI must not run')):
                    for args in (['audit', agent], ['audit', agent, '--reviewer', agent], ['watch', '--only', str(sb.project)]):
                        code, out, err = sb.cli(args, reviewer=reviewer, delivery=delivery)
                        self.assertEqual(code, 7, out + err)
                        self.assertIn('scrubbed digest of finished turns', err)
                        self.assertIn(other if '--reviewer' not in args else agent, err)
                        if '--reviewer' not in args:
                            self.assertIn(vendor, err)
                    with self.assertRaisesRegex(ReviewError, 'consent'):
                        ClaudeReviewer(config.merge(config.DEFAULTS, {}), State(sb.root/'own')).review('SYSTEM', 'DATA')
                self.assertFalse(reviewer.calls or delivery.calls)
                self.assertFalse((sb.fake/'calls.jsonl').exists())
                self.assertFalse(State(sb.root/'state/sidecarwatch').data['reviews'])

    def test_explicit_consent_persists_only_in_user_config_and_status(self):
        with Sandbox(consent=False) as sb:
            codex_session(sb.codex, sb.project, [('Add CSV export.', 'Done.')])
            self.assertIn('OFF', sb.cli(['status'])[1])
            self.assertEqual(sb.cli(['init'])[0], 0)
            self.assertIn('OFF', sb.cli(['status'])[1])
            self.assertEqual(sb.cli(['init', '--allow-cross-vendor-review'])[0], 0)
            self.assertIn('ON', sb.cli(['status'])[1])
            cfg = sb.root/'config/sidecarwatch/config.toml'
            self.assertTrue(config.load(cfg)['privacy']['allow_cross_vendor_review'])
            self.assertEqual(sb.cli(['audit', 'codex'], reviewer=FakeReviewer([verdict()]))[0], 0)
            self.assertNotIn('allow_cross_vendor_review', (sb.root/'state/sidecarwatch/state.json').read_text())

    def test_watch_flag_saves_consent_and_revisits_unhandled_turn(self):
        with Sandbox(consent=False) as sb:
            codex_session(sb.codex, sb.project, [('Add CSV export.', 'Done.')])
            reviewer = FakeReviewer([verdict()])
            self.assertEqual(sb.cli(['watch', '--only', str(sb.project)], reviewer=reviewer)[0], 7)
            self.assertEqual(sb.cli(['watch', '--only', str(sb.project), '--allow-cross-vendor-review'], reviewer=reviewer)[0], 0)
            self.assertEqual(len(reviewer.calls), 1)
            self.assertIn('ON', sb.cli(['status'])[1])

    def test_dry_run_is_local_with_or_without_consent_and_marks_handled(self):
        for consent in (False, True):
            with self.subTest(consent=consent), Sandbox(consent=consent) as sb:
                codex_session(sb.codex, sb.project, [('Add CSV export.', 'The test passed.')])
                reviewer, delivery = FakeReviewer([]), FakeDelivery()
                with patch('sidecarwatch.reviewer.run', side_effect=AssertionError('CLI must not run')):
                    args = ['watch', '--only', str(sb.project), '--dry-run', '--auto-reply', '--allow', str(sb.project)]
                    code, out, err = sb.cli(args, reviewer=reviewer, delivery=delivery)
                    self.assertEqual(code, 0, out + err)
                    self.assertIn('WOULD send', out)
                    self.assertIn('Add CSV export.', out)
                    self.assertEqual(sb.cli(args, reviewer=reviewer)[0], 0)
                    self.assertEqual(sb.cli(['audit', 'codex', '--dry-run'], reviewer=reviewer)[0], 0)
                self.assertFalse(reviewer.calls or delivery.calls)
                self.assertFalse((sb.fake/'calls.jsonl').exists())
                self.assertFalse(State(sb.root/'state/sidecarwatch').data['reviews'])

    def test_extra_envelope_keys_and_observed_message_metadata_are_supported(self):
        # Keys/types came from structure inspection. Every value below is invented.
        for agent, make, parse in [('claude', claude_session, claude_parse), ('codex', codex_session, codex_parse)]:
            with self.subTest(agent=agent), Sandbox() as sb:
                clock = FakeClock()
                path = make(getattr(sb, agent), sb.project, [('Add CSV export.', 'The CSV test passed.')], at=clock.now())
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                for row in rows:
                    row.update(future_envelope={'public': 1}, ordinal=1)
                    if agent == 'claude':
                        row.update(sessionId=path.stem, parentUuid=None, gitBranch='example', version='example', userType='example', permissionMode='example')
                        row['message'].update(stop_sequence=None, stop_details=None, container=None, context_management=None, diagnostics=None, input_transformations=[])
                    elif row['type'] == 'session_meta':
                        row['payload'].update(cli_version='example', model_provider='example', base_instructions={'text': 'Example.'}, git={'branch': 'example'})
                    elif row['payload']['type'] == 'user_message':
                        row['payload'].update(images=[], local_images=[], text_elements=[])
                    elif row['payload']['type'] == 'agent_message':
                        row['payload']['phase'] = 'final_answer'
                write_lines(path, rows)
                session = parse(path, clock.now(), 60)
                self.assertFalse(session.evidence.unsupported)
                self.assertTrue(session.evidence.safe, session.evidence.reason)
                reviewer = FakeReviewer([verdict()])
                self.assertEqual(sb.cli(['watch', '--only', str(sb.project)], clock=clock, reviewer=reviewer)[0], 0)
                self.assertEqual(len(reviewer.calls), 1)

    def test_one_sensitive_span_does_not_blank_the_conversation_or_title(self):
        with Sandbox() as sb:
            value = 'Tulip' + 'River77!'
            first = 'Add CSV export.\n' + text_parts('pa', 'ssword=') + value
            claude_session(sb.claude, sb.project, [(first, 'The CSV test passed.'), ('Check the CSV header.', 'The header is correct.')])
            for args in (['show', 'claude'], ['show', 'claude', '--json'], ['sessions']):
                code, out, err = sb.cli(args)
                self.assertEqual(code, 0, err)
                self.assertNotIn(value, out)
                self.assertIn('Add CSV export.', out)
                if args[0] == 'show':
                    self.assertIn('The CSV test passed.', out)
                    self.assertIn('The header is correct.', out)
                    self.assertIn('[REDACTED:assignment]', out)
            code, out, err = sb.cli(['audit', 'claude', '--list-only'])
            self.assertEqual(code, 8)
            for phrase in ('deliberate privacy refusal', 'rule class: assignment', 'Remove sensitive material'):
                self.assertIn(phrase, err)
            self.assertNotIn(value, out + err)

    def test_full_ids_and_ambiguous_prefix_candidates(self):
        with Sandbox() as sb:
            ids = ['shared-prefix-one', 'shared-prefix-two']
            for sid in ids:
                claude_session(sb.claude, sb.project, [('Add CSV export.', 'Done.')], id=sid)
            listing = sb.cli(['sessions'])[1]
            code, out, err = sb.cli(['show', 'shared-prefix'])
            self.assertEqual(code, 4)
            for sid in ids:
                self.assertIn(sid, listing)
                self.assertIn(sid, err)

    def test_doctor_capability_and_login_causes_match_review(self):
        for marker, phrase in [('no-mode', 'missing non-interactive capability'), ('logged-out', 'not logged in')]:
            with self.subTest(marker=marker), Sandbox() as sb:
                (sb.fake/marker).touch()
                codex_session(sb.codex, sb.project, [('Add CSV export.', 'Done.')])
                code, out, err = sb.cli(['doctor', '--json'])
                self.assertEqual(code, 0, err)
                row = json.loads(out)['clis']['claude']
                self.assertFalse(row['found'])
                self.assertIn(phrase, row['reviewer']['reason'])
                code, out, err = sb.cli(['audit', 'codex'])
                self.assertEqual(code, 5)
                self.assertIn(phrase, err)
                self.assertNotIn('Sending a scrubbed digest', err)
                calls = [json.loads(line) for line in (sb.fake/'calls.jsonl').read_text().splitlines()]
                self.assertTrue(all('--help' in c['argv'] or '--version' in c['argv'] or 'status' in c['argv'] for c in calls))

    def test_corrupt_path_notice_once_and_config_syntax_is_distinct(self):
        with Sandbox(consent=False) as sb:
            path = claude_session(sb.claude, sb.project, [('Add CSV export.', 'Done.')])
            with path.open('a') as stream:
                stream.write('malformed\n' * 3)
            first = sb.cli(['sessions'])[2]
            self.assertIn(str(path), first)
            self.assertEqual(first.count(str(path)), 1)
            self.assertNotIn('malformed\n', first)
            self.assertEqual(sb.cli(['sessions'])[2], '')
            cfg = sb.root/'invalid.toml'
            cfg.write_text('broken = [')
            code, out, err = sb.cli(['sessions', '--config', str(cfg)])
            self.assertEqual(code, 2)
            self.assertIn('invalid TOML', err)
            self.assertNotIn('cannot read', err)
            cfg.unlink()
            self.assertIn('cannot read config', sb.cli(['sessions', '--config', str(cfg)])[2])
            sb.cli(['stop'])
            self.assertIn('Kill switch cleared', sb.cli(['resume'])[1])
