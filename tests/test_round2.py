"""Round two regressions use invented sensitive values assembled at runtime."""
import datetime as dt
import json
import unittest
from unittest.mock import patch

from sidecarwatch.scrub import scrub
from tests.helpers import (Sandbox, FakeClock, FakeReviewer, FakeDelivery,
                           claude_session, codex_session, verdict, text_parts)


def card_number(length=16):
    # Generate a checksum-valid synthetic candidate, never a source literal.
    body = '4' + '1' * (length - 2)
    for last in range(10):
        value = body + str(last)
        total = 0
        for index, char in enumerate(reversed(value)):
            digit = int(char) * (2 if index % 2 else 1)
            total += digit - 9 if digit > 9 else digit
        if total % 10 == 0:
            return value
    raise AssertionError('could not build synthetic checksum')


def private_values():
    card = card_number()
    compact = 'DE' + '89' + ''.join(str(i % 10) for i in range(18))
    return [('ssn', '-'.join(('123', '45', '6789'))),
            ('ssn', ' '.join(('123', '45', '6789'))),
            ('card', card), ('card', ' '.join(card[i:i+4] for i in range(0, len(card), 4))),
            ('card', '-'.join(card[i:i+4] for i in range(0, len(card), 4))),
            ('iban', compact), ('iban', ' '.join(compact[i:i+4] for i in range(0, len(compact), 4))),
            ('email', text_parts('reader', '@', 'example.com'))]


class RoundTwoTests(unittest.TestCase):
    def test_personal_data_patterns_strict_display_and_structures(self):
        for kind, value in private_values():
            for text in (value, 'Here is ' + value + '.', json.dumps({'value': value})):
                with self.subTest(kind=kind, text=text):
                    self.assertNotIn(value, scrub(text, strict=True))
                    self.assertIn('[REDACTED:' + kind + ']', scrub(text, strict=True))
        for length in (13, 15, 16, 19):
            value = card_number(length)
            self.assertEqual(scrub(value, strict=True), '[REDACTED:card]')
        identifier = '-'.join(('0' * 7 + '1', '1' * 4, '4' + '1' * 3, '8' + '1' * 3, '1' * 12))
        self.assertEqual(scrub(identifier, strict=True), identifier)
        self.assertEqual(scrub('Session ' + identifier, strict=True), 'Session ' + identifier)
        unusual = card_number(19)
        grouped = '-'.join((unusual[:3], unusual[3:5], unusual[5:9], unusual[9:13], unusual[13:]))
        self.assertEqual(scrub(grouped, strict=True), '[REDACTED:card]')
        number = card_number()
        bad = number[:-1] + str((int(number[-1]) + 1) % 10)
        self.assertEqual(scrub(bad, strict=True), bad)
        self.assertEqual(scrub(' '.join(bad[i:i+4] for i in range(0, len(bad), 4))), '[REDACTED:card]')
        self.assertIn('[REDACTED:card]', scrub(json.dumps({'value': int(number)}), strict=True))
        self.assertEqual(scrub('２０２６-１０-１０'), '2026-10-10')
        fullwidth = ''.join(chr(ord(char) + 0xFEE0) for char in number)
        self.assertEqual(scrub(fullwidth, strict=True), '[REDACTED:card]')
        self.assertEqual(scrub(number[:8] + '\u200b' + number[8:], strict=True), '[REDACTED:card]')
        address = private_values()[-1][1]
        self.assertEqual(scrub(address, {'emails': False, 'extra_patterns': [], 'extra_terms': []}), address)

    def test_personal_data_never_reaches_preview_reviewer_or_local_logs(self):
        for dry in (True, False):
            with self.subTest(dry=dry), Sandbox() as sb:
                values = private_values()
                request = 'Sample values: ' + '; '.join(value for _, value in values)
                claude_session(sb.claude, sb.project, [(request, 'The CSV test passed.')])
                reviewer, delivery = FakeReviewer([verdict()]), FakeDelivery()
                args = ['watch', '--only', str(sb.project)] + (['--dry-run'] if dry else [])
                code, out, err = sb.cli(args, reviewer=reviewer, delivery=delivery)
                self.assertEqual(code, 0, out + err)
                displayed = sb.cli(['show', 'claude'])[1]
                listing = sb.cli(['sessions'])[1]
                payloads = out + err + displayed + listing + ''.join(body for _, body in reviewer.calls)
                for path in (sb.root/'state/sidecarwatch').glob('*'):
                    if path.is_file():
                        payloads += path.read_text()
                for _, value in values:
                    self.assertNotIn(value, payloads)
                self.assertEqual(len(reviewer.calls), int(not dry))
                self.assertFalse(delivery.calls)

    def test_unchanged_privacy_refusal_keeps_exit_and_remedy_until_edit(self):
        for consent in (False, True):
            with self.subTest(consent=consent), Sandbox(consent=consent) as sb:
                clock = FakeClock()
                value = text_parts('Tulip', 'River77!')
                path = claude_session(sb.claude, sb.project,
                                      [(text_parts('pa', 'ssword=') + value, 'Done.')], at=clock.now())
                args = ['watch', '--only', str(sb.project)]
                reviewer = FakeReviewer([])
                with patch('sidecarwatch.reviewer.run', side_effect=AssertionError('must stay local')):
                    for flags in (['--dry-run'], [], ['--dry-run', '--json'], ['--json']):
                        code, out, err = sb.cli(args + flags, clock=clock, reviewer=reviewer)
                        self.assertEqual(code, 8, out + err)
                        self.assertIn('rule class: assignment', out + err)
                        self.assertIn('Remove sensitive material at its source', out + err)
                        self.assertNotIn('exclude this session', out + err)
                        self.assertNotIn('worker-needs-human', out + err)
                        self.assertNotIn(value, out + err)
                        if '--json' in flags:
                            self.assertEqual(json.loads(out)['results'][0]['action'], 'refused')
                    sb.cli(['resolve', path.stem])
                    self.assertEqual(sb.cli(args, clock=clock, reviewer=reviewer)[0], 8)
                path.write_text(path.read_text().replace(text_parts('pa', 'ssword=') + value, 'Add CSV export.'))
                self.assertEqual(sb.cli(['audit', 'claude', '--dry-run'], clock=clock)[0], 0)
                code, out, err = sb.cli(args, clock=clock, reviewer=FakeReviewer([verdict()]))
                self.assertEqual(code, 0 if consent else 7, out + err)

    def test_future_timestamp_withheld_in_both_modes_and_orders(self):
        for make, agent in ((claude_session, 'claude'), (codex_session, 'codex')):
            for first in (True, False):
                with self.subTest(agent=agent, first_dry=first), Sandbox(consent=False) as sb:
                    clock = FakeClock()
                    make(getattr(sb, agent), sb.project, [('Add CSV export.', 'The CSV test passed.')],
                         at=clock.now() + dt.timedelta(hours=6))
                    reviewer = FakeReviewer([])
                    with patch('sidecarwatch.reviewer.run', side_effect=AssertionError('must stay local')):
                        for dry in (first, not first):
                            code, out, err = sb.cli(['watch', '--only', str(sb.project), '--json'] +
                                                   (['--dry-run'] if dry else []), clock=clock, reviewer=reviewer)
                            expected_count = 0 if dry and first else 1
                            self.assertEqual(code, 10 if expected_count else 0, out + err)
                            self.assertEqual(json.loads(out)['need_you'], expected_count)
                            self.assertEqual(json.loads(out)['results'][0]['reason'], 'invalid-timestamps')
                            self.assertEqual(json.loads(out)['results'][0]['action'], 'local-preview' if dry else 'flagged')
                    self.assertFalse(reviewer.calls)
                    self.assertIn('invalid-timestamps', sb.cli(['open'], clock=clock)[1])

    def test_watch_open_status_share_all_unresolved_items_across_scopes(self):
        with Sandbox() as sb:
            for index in range(3):
                claude_session(sb.claude, sb.project, [('Add CSV export.', 'Done.')], id='example-worker-' + str(index))
            args = ['watch', '--only', str(sb.project)]
            code, out, err = sb.cli(args, reviewer=FakeReviewer([verdict(kind='2')] * 3))
            self.assertEqual(code, 10, out + err)
            for flags in ([], ['--json']):
                code, out, err = sb.cli(args + flags, reviewer=FakeReviewer([]))
                self.assertEqual(code, 10, out + err)
                self.assertEqual(json.loads(out)['need_you'], 3) if flags else self.assertIn('3 need you', out)
            code, out, err = sb.cli(['watch', '--session', 'example-worker-0', '--json'], reviewer=FakeReviewer([]))
            self.assertEqual(code, 10, out + err)
            self.assertEqual(json.loads(out)['need_you'], 3)
            self.assertIn('3 open', sb.cli(['open'])[1])
            code, out, err = sb.cli(['status', '--json'])
            self.assertEqual(code, 10, out + err)
            self.assertEqual(json.loads(out)['need_you'], 3)
            for index in range(3):
                self.assertEqual(sb.cli(['resolve', 'example-worker-' + str(index)])[0], 0)
            self.assertEqual(sb.cli(['status'])[0], 0)
            self.assertIn('0 open', sb.cli(['open'])[1])
            self.assertIn('0 need you', sb.cli(args, reviewer=FakeReviewer([]))[1])

    def test_errors_name_action_and_config_path(self):
        with Sandbox(consent=False) as sb:
            claude_session(sb.claude, sb.project, [('Add CSV export.', 'Done.')])
            self.assertIn('existing folder', sb.cli(['watch', '--only', str(sb.root/'missing')])[2])
            self.assertIn('at least six characters', sb.cli(['audit', 'abcd'])[2])
            self.assertIn('not found', sb.cli(['resolve', 'missing-id'])[2])
            path = sb.root/'bad.toml'
            for text in ('bogus=true', '[reviewer]\nmax_per_hour="wrong"'):
                path.write_text(text)
                code, out, err = sb.cli(['status', '--config', str(path)])
                self.assertEqual(code, 2, out + err)
                self.assertIn(str(path), err)
