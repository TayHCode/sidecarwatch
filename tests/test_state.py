import stat
import unittest
from sidecarwatch.state import State, Busy
from tests.helpers import Sandbox


class StateTests(unittest.TestCase):
    def test_atomic_private_and_lock(self):
        with Sandbox() as sb:
            s = State(sb.root / 'own')
            s.save()
            self.assertEqual(stat.S_IMODE(s.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((s.root/'state.json').stat().st_mode), 0o600)
            lock = s.acquire()
            try:
                with self.assertRaises(Busy):
                    State(s.root).acquire()
            finally:
                lock.close()
            s.write('state.json', 'bad')
            self.assertEqual(State(s.root).data['sessions'], {})
            s.log({'action': 'printed'}, {'max_bytes': 1})
            s.log({'action': 'printed'}, {'max_bytes': 1})
            self.assertTrue((s.root/'log.jsonl.1').exists())
