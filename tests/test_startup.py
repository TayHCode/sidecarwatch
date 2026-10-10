import unittest
from unittest.mock import patch

import sidecarwatch


class StartupTests(unittest.TestCase):
    def test_old_python_stops_before_cli_import(self):
        with patch('sys.version_info', (3, 9, 6)), patch('sys.stderr') as error:
            with self.assertRaises(SystemExit) as result:
                sidecarwatch.entry()
        self.assertEqual(result.exception.code, 2)
        self.assertIn('Python 3.11', ''.join(call.args[0] for call in error.write.call_args_list))

    def test_native_windows_stops_before_posix_import(self):
        with patch('os.name', 'nt'), patch('sys.stderr') as error:
            with self.assertRaises(SystemExit) as result:
                sidecarwatch.entry()
        self.assertEqual(result.exception.code, 2)
        self.assertIn('Native Windows is unsupported', ''.join(call.args[0] for call in error.write.call_args_list))
