import shutil
import socket
import unittest
from tests.helpers import Sandbox


class HarnessTests(unittest.TestCase):
    def test_harness_guard(self):
        with Sandbox() as sb:
            for name in ['claude', 'codex']:
                self.assertEqual(shutil.which(name), str(sb.root/'bin'/name))
            with socket.socket() as probe:
                with self.assertRaisesRegex(AssertionError, 'network blocked'):
                    probe.connect(('127.0.0.1', 9))
