from tests.helpers import text_parts
import json
import unittest
from sidecarwatch.scrub import scrub
from sidecarwatch.config import DEFAULTS
from tests.helpers import Sandbox, FakeReviewer, codex_session, claude_session, nudge, write_lines


def cases():
    secret = 'Tulip' + 'River77!'
    opaque = 'Qw9rT2yZ' * 5
    return [
        (text_parts('json-pa', 'ssword'), json.dumps({text_parts('pa', 'ssword'): secret}), secret),
        (text_parts('json-ke', 'y'), text_parts('"api_ke', 'y": "')+secret+'"', secret),
        ('python-dict', text_parts("{'to", "ken': '")+secret+"'}", secret),
        ('prose', text_parts('the pa', 'ssword is ')+secret, secret),
        ('short-flag', 'mysql -p'+secret, secret),
        ('long-flag', text_parts('--pa', 'ssword ')+secret, secret),
        ('curl-user', 'curl -u user:'+secret, secret),
        (text_parts('ba', 'sic'), text_parts('Au', 'thorization: Ba', 'sic ')+opaque, opaque),
        ('hex', 'Value '+('deadbeef'*5), 'deadbeef'*5),
        (text_parts('unfinished-ke', 'y'), '-----BEGIN '+text_parts('RSA PR', 'IVATE KE', 'Y-----\n')+opaque, opaque),
        ('short-value', text_parts(text_parts('pa', 'ssword'), '=7'), '7'),
        ('unknown-variable', 'BUILD_VALUE='+opaque, opaque),
        ('quoted-spaces', text_parts('pa', 'ssphrase="')+secret+' tail words"', 'tail words'),
        ('env', text_parts('APP_SE', 'CRET=')+secret, secret),
        ('yaml', text_parts('api_ke', 'y: ')+secret, secret),
        ('url', text_parts('https', '://user:')+secret+'@'+'host.invalid/path', secret),
        (text_parts('be', 'arer'), text_parts('Au', 'thorization: Be', 'arer ')+secret.rstrip('!'), secret.rstrip('!')),
        (text_parts('escaped-ke', 'y'), '{"pass\\u0077ord":"'+secret+'"}', secret),
        ('nested', json.dumps({text_parts('se', 'cret'): [{'value': secret}, {'value': opaque}]}), opaque),
        ('yaml-block', text_parts(text_parts('pa', 'ssword'), ': |\n  ')+secret+'\n  second part\nname: example\n', 'second part'),
        ('yaml-list', text_parts('to', 'kens:\n- ')+secret+'\n- '+opaque+'\nname: example\n', opaque),
        ('cookie', 'Cookie: session='+secret+'; theme=dark', secret),
        ('url-query', text_parts('ht', 'tps://host.invalid/?access_to', 'ken=')+secret+'&page=2', secret),
        ('prefix', 'rk_'+'live_'+'Yp5Q'*6, 'Yp5Q'*6),
        ('numeric', json.dumps({text_parts('cr', 'edentials'): 123456}), '123456'),
        ('base64', opaque+'==', opaque),
        ('broken-json', text_parts('{"pa', 'ssword": "')+secret, secret),
    ]


class ScrubSafetyTests(unittest.TestCase):
    def test_twenty_seven_secret_forms(self):
        self.assertEqual(len(cases()), 27)
        for name, text, secret in cases():
            with self.subTest(name=name):
                result = scrub(text, DEFAULTS['scrub'])
                self.assertNotIn(secret, result)
                self.assertIn('[REDACTED:', result)
                self.assertNotIn(secret, scrub(result, DEFAULTS['scrub']))

    def test_whole_sensitive_values_and_uncertain_inputs(self):
        for text in [text_parts('prefix {', text_parts('"to', 'ken":'), ' ["one h', 'idden", ', '"two hid', 'den"]}'),
                     text_parts(text_parts('pa', 'ssword'), '="one hi', 'dden\ntwo', ' hidden"'), text_parts('pa', 'ssphrase: one hidden two hidden'),
                     'MYSTERY_VALUE=hidden', 'Cookie: first=hidden; second=also hidden',
                     '{"safe":' + '['*100000 + '"hidden"' + ']'*100000 + '}',
                     'x'*300000]:
            clean = scrub(text)
            self.assertNotIn('hidden', clean)
            self.assertIn('[REDACTED:', clean)
        cfg = {**DEFAULTS['scrub'], 'extra_patterns': ['[']}
        self.assertEqual(scrub('Do not return this after an error', cfg), '[REDACTED:scrub-failed]')

    def test_secrets_absent_from_digest_output_and_log(self):
        for name, text, secret in cases():
            with self.subTest(name=name), Sandbox() as sb:
                # Single-character and numeric needles are checked directly above;
                # whole state contains timestamps and unrelated numeric metadata.
                codex_session(sb.codex, sb.project, [('Goal '+text, 'Result '+text)])
                fake = FakeReviewer([nudge(summary=text, reply=text, question_for_user=text)])
                code, out, err = sb.cli(['watch', '--only', str(sb.project)], reviewer=fake)
                shown = sb.cli(['show', 'codex'])[1]
                state_text = ''.join(p.read_text() for p in (sb.root/'state/sidecarwatch').glob('*') if p.is_file())
                if len(secret) > 6:
                    for surface in (fake.calls[0][1] if fake.calls else '', out, shown, state_text):
                        self.assertNotIn(secret, surface, name)

    def test_tool_tokens_crossing_old_cut_are_scrubbed_first(self):
        token = 'sk-'+'Ab7x'*8
        for agent in ('claude', 'codex'):
            for pad in (135, 141, 150):
                with self.subTest(agent=agent, pad=pad), Sandbox() as sb:
                    detail = 'safe '* (pad//5) + token
                    if agent == 'claude':
                        path = claude_session(sb.claude, sb.project, [('Goal', 'Done')])
                        rows = [json.loads(line) for line in path.read_text().splitlines()]
                        rows.insert(1, {'type':'assistant', 'message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':detail}}]}})
                    else:
                        path = codex_session(sb.codex, sb.project, [('Goal','Done')], shape='B')
                        rows = [json.loads(line) for line in path.read_text().splitlines()]
                        rows.insert(2, {'type':'response_item','payload':{'type':'function_call','name':'shell','arguments':json.dumps({'command':detail})}})
                    write_lines(path, rows)
                    fake = FakeReviewer([nudge()])
                    sb.cli(['audit', agent], reviewer=fake)
                    shown = sb.cli(['show', agent, '--tools'])[1]
                    for surface in (shown, fake.calls[0][1] if fake.calls else ''):
                        self.assertNotIn(token[:8], surface)

    def test_json_structure_survives_safe_input(self):
        text = json.dumps({'public': {'answer': 42}, text_parts('pa', 'ssword'): 'abc'})
        self.assertEqual(scrub(text), '[REDACTED:assignment]')
        self.assertEqual(json.loads(scrub(json.dumps({'public': {'answer': 42}}))), {'public': {'answer': 42}})
        sid = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
        self.assertEqual(scrub(sid), sid)
