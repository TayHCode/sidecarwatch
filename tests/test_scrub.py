from tests.helpers import text_parts
from pathlib import Path
import unittest
from sidecarwatch.config import merge, DEFAULTS
from sidecarwatch.scrub import scrub
from tests.helpers import Sandbox


class ScrubTests(unittest.TestCase):
    def test_scrub_patterns(self):
        cfg = merge(DEFAULTS, {})['scrub']
        cases = [(text_parts('pr', 'ivate-ke', 'y'), '-----BEGIN ' + text_parts('PR', 'IVATE KE', 'Y-----\nabc\n-----END ') + text_parts('PR', 'IVATE KE', 'Y-----')), (text_parts('aws-ke', 'y'), 'AK'+'IA'+'A'*16), (text_parts('github-to', 'ken'), 'gh'+'p_'+'a'*30), (text_parts('github-to', 'ken'), 'github_'+'pat_'+'a'*22), (text_parts('api-ke', 'y'), 'sk'+'-'+'a'*24), (text_parts('slack-to', 'ken'), 'xox'+'b-'+'a'*12), (text_parts('google-ke', 'y'), 'AI'+'za'+'a'*35), ('jwt', 'ey'+'J'+'a'*11+'.'+'b'*11+'.'+'c'*11), (text_parts('be', 'arer'), text_parts('be', 'arer ')+'a'*20), ('url-login', text_parts('ht', 'tps://name:')+ 'abcdef'+'@'+'host.invalid'), ('assignment', text_parts('pa', 'ssword=')+'abcdefgh'), ('email', 'reader'+'@'+'example.com')]
        for kind, value in cases:
            with self.subTest(kind=kind):
                self.assertIn('[REDACTED:'+kind+']', scrub(value, cfg))
                self.assertEqual(scrub('ordinary sample', cfg), 'ordinary sample')
        cfg['extra_terms'] = ['hidden-name']
        self.assertEqual(scrub('hidden-name', cfg), '[REDACTED:custom]')
        self.assertEqual(scrub(text_parts(text_parts('to', 'ken=[R'), 'EDACTED:', text_parts('api-ke', 'y]')), cfg), text_parts(text_parts('to', 'ken=[R'), 'EDACTED:', text_parts('api-ke', 'y]')))
        with Sandbox():
            self.assertEqual(scrub(str(Path.home())+'/project', cfg), '~/project')
