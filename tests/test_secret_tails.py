from tests.helpers import text_parts
import json
import unittest
from sidecarwatch.scrub import scrub, ScrubUncertain
from tests.helpers import Sandbox, FakeReviewer, FakeDelivery, codex_session, claude_session, nudge

secret = 'Cedar' + 'Brook79!'
tail = 'Hidden' + 'Tail62'
opaque = 'Zw3wF2kY' * 5
old17 = [(text_parts('json-pa', 'ssword'), json.dumps({text_parts('pa', 'ssword'): secret}), secret), (text_parts('json-ke', 'y'), text_parts('"api_ke', 'y": "') + secret + '"', secret), ('python-dict', text_parts("{'to", "ken': '") + secret + "'}", secret), (text_parts('prose-pa', 'ssword'), text_parts('the pa', 'ssword is ') + secret, secret), ('cli-short', text_parts('mysql ', '-p') + secret, secret), ('cli-long', text_parts('--pa', 'ssword ') + secret, secret), ('curl-user', 'curl -u user:' + secret, secret), (text_parts('ba', 'sic-header'), text_parts('Au', 'thorization: Ba', 'sic ') + opaque, opaque), ('long-hex', text_parts('To', 'ken ') + 'feebdaed' * 5, 'feebdaed' * 5), ('pem-cut', text_parts('-----BEG', 'IN RSA P', 'RIVATE K', 'EY-----\n') + opaque, opaque), ('short-value', text_parts(text_parts('pa', 'ssword'), '=abc'), 'abc'), ('unknown-env', 'MYSTERY_VALUE=' + opaque, opaque), (text_parts('spaced-pa', 'ssphrase'), text_parts('pa', 'ssphrase="') + secret + ' ' + tail + '"', tail), ('env', text_parts('APP_SE', 'CRET=') + secret, secret), ('yaml', text_parts('api_ke', 'y: ') + secret, secret), (text_parts('cr', 'edential-url'), text_parts('ht', 'tps://user:') + secret + '' + '@' + 'host.invalid/path', secret), (text_parts('be', 'arer'), text_parts('Au', 'thorization: Be', 'arer ') + secret[:-1], secret[:-1])]
new20 = [(text_parts('escaped-json-ke', 'y'), '{"pass\\u0077ord":"' + secret + '"}', secret), (text_parts('fullwidth-json-ke', 'y'), json.dumps({'ｐａｓｓｗｏｒｄ': secret}, ensure_ascii=False), secret), (text_parts('cyrillic-json-ke', 'y'), json.dumps({'passwоrd': secret}, ensure_ascii=False), secret), (text_parts('zero-width-json-ke', 'y'), json.dumps({'pass\u200bword': secret}, ensure_ascii=False), secret), ('yaml-block', text_parts(text_parts('pa', 'ssword'), ': |\n  ') + secret + '\n  ' + tail + '\nname: public', tail), ('yaml-folded', text_parts(text_parts('pa', 'ssword'), ': >-\n  ') + secret + '\n  ' + tail + '\nname: public', tail), ('yaml-comma', text_parts('pa', 'ssword: ') + secret + ',' + tail, tail), ('yaml-semicolon', text_parts('pa', 'ssword: ') + secret + ';' + tail, tail), ('env-continuation', text_parts('DB_PA', 'SSWORD=') + secret + '\\\n' + tail, tail), ('cli-escaped-space', text_parts('--pa', 'ssword ') + secret + '\\ ' + tail, tail), (text_parts('folded-au', 'th'), text_parts(text_parts('Au', 'thoriz'), 'ation: B', 'asic Qm9', 'ndXM=\n\t') + tail, tail), ('folded-cookie', 'Cookie: sid=' + secret + ';\n\tother=' + tail, tail), (text_parts('cr', 'edential-url-encoded'), text_parts('ht', 'tps://user:') + secret + '%40zone' + '@' + 'host.invalid/path', secret), ('pem-openssh', text_parts('-----BEG', 'IN OPENS', 'SH PRIVA', text_parts('TE KE', 'Y--'), '---\n') + secret + '\n' + tail + text_parts('\n-----END OPENSSH PR', 'IVATE KE', 'Y-----'), tail), ('pem-split-lines', text_parts('-----BEG', 'IN PRIVA', text_parts('TE KE', 'Y--'), '---\n') + secret + '\n' + tail, tail), (text_parts('to', 'ken-prefix'), 'hf_' + opaque, opaque), ('long-hex', 'cafe' * 20, 'cafe' * 20), ('long-base64', opaque + '==', opaque), ('fullwidth-colon', text_parts('pa', 'ssword： ') + secret, secret), ('split-prose', text_parts('The pa', 'ssword is ') + secret + '\n' + tail, tail)]
extra = [
    ('words', text_parts('pa', 'ssword: ')+secret+' '+tail, tail),
    ('comment', text_parts('# pa', 'ssword = ')+secret+' '+tail, tail),
    ('inline-comment', text_parts('setting = 1 # to', 'ken: ')+secret+' '+tail, tail),
    ('masked', text_parts(text_parts('pa', 'ssword'), ': *** ')+tail, tail),
    ('masked-prefix', 'sk-'+'*** '+tail, tail),
    ('quoted-shape', text_parts('"pa', 'ssword": "')+secret+' '+tail+'"', tail),
    ('escaped-shape', text_parts('{\\"pa', 'ssword\\": \\"') + secret + ' ' + tail + r'\"}', tail),
    ('nested-string', json.dumps({'public': text_parts('pa', 'ssword: ')+secret+'\n'+tail}), tail),
    ('base64-tail', text_parts('Au', 'thorization: ')+opaque+'\n'+tail, tail),
    ('hex-tail', text_parts('to', 'ken: ')+'cafe'*20+' '+tail, tail),
    ('json-newlines', json.dumps({text_parts('pa', 'ssword'): secret+'\n'+tail}), tail),
    ('json-comments', text_parts('{"pa', 'ssword": "')+secret+'" /* '+tail+' */}', tail),
    ('shell-quote', text_parts('DB_PA', 'SSWORD="')+secret+'" "'+tail+'"', tail),
    ('comment-newline', text_parts('// pa', 'ssword: ')+secret+'\n'+tail, tail),
]


class SecretTailTests(unittest.TestCase):
    def test_all_surfaces(self):
        self.assertEqual((len(old17), len(new20), len(extra)), (17, 20, 14))
        for name, text, needle in old17 + new20 + extra:
            for agent, make in (('codex', codex_session), ('claude', claude_session)):
                with self.subTest(case=name, agent=agent), Sandbox() as sb:
                    make(getattr(sb, agent), sb.project, [(text, text)])
                    cfg = sb.root/'cfg.toml'; cfg.write_text('[log]\ntext="digest"\n')
                    value = nudge(summary=text, reply=text, question_for_user=text)
                    reviewer, delivery = FakeReviewer([value]), FakeDelivery()
                    code, out, err = sb.cli(['watch', '--only', str(sb.project), '--config', str(cfg)],
                                            reviewer=reviewer, delivery=delivery)
                    shown = sb.cli(['show', agent, '--chars', '100000'])[1]
                    root = sb.root/'state/sidecarwatch'
                    stored = ''.join(p.read_text() for p in root.glob('*') if p.is_file())
                    surfaces = [scrub(text), out, shown, stored] + [call[1] for call in reviewer.calls]
                    for clean in surfaces:
                        self.assertNotIn(needle, clean)
                    self.assertFalse(delivery.calls)
                    try:
                        scrub(text, strict=True)
                    except ScrubUncertain:
                        self.assertEqual(code, 8, err)
                        self.assertFalse(reviewer.calls)
                        self.assertIn('scrub-uncertain', (root/'needs-you.md').read_text())
                        self.assertNotIn('Sending a scrubbed digest', err)
                    other = FakeReviewer([value])
                    audit_code, audit_out, audit_err = sb.cli(['audit', agent], reviewer=other)
                    for clean in [audit_out, audit_err] + [call[1] for call in other.calls]:
                        self.assertNotIn(needle, clean)
                    if not reviewer.calls:
                        self.assertEqual(audit_code, 8)
                        self.assertFalse(other.calls)
                    self.assertFalse((sb.fake/'calls.jsonl').exists())

    def test_ambiguous_scrubbing_cannot_call_reviewer_even_beyond_caps(self):
        for text in ('safe ' * 3000 + text_parts('pa', 'ssword: ')+secret+'\n'+tail,
                     '{"safe":'+ '['*100 + '"'+secret+'"'+']'*100+'}',
                     'z'*300000, text_parts('{"pa', 'ssword":"')+secret):
            with Sandbox() as sb:
                codex_session(sb.codex, sb.project, [('Goal', text)])
                reviewer = FakeReviewer([])
                code, out, err = sb.cli(['audit', 'codex'], reviewer=reviewer)
                self.assertEqual(code, 8)
                self.assertFalse(reviewer.calls)
                self.assertNotIn(secret, out+err)
