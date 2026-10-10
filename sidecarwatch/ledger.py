"""Durable accounting for automatic attempts, independent of transcript echoes."""
import hashlib, json, os
from pathlib import Path
import secrets
from .scrub import bounded_json
from .storage import write_private
from . import APP_NAME

MARKER = 'SIDECARWATCH-AUTO-SEND-v1'
DAY_CAP = 100

class LedgerError(ValueError):
    pass

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def session_key(session):
    return digest([session.agent, session.id])

class SendLog:
    def __init__(self, state):
        self.state = state
        self.path = state.root / 'sends.jsonl'
        self.anchor = state.root / 'send-anchor.json'
        # Fixed HOME location, outside configurable/disposable state. No secrets.
        self.witness = Path.home() / '.config' / APP_NAME / 'accounting' / (digest(str(state.root.resolve())) + '.json')
    def read(self):
        try:
            with self.anchor.open() as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            anchor = bounded_json(raw)
            if not isinstance(anchor, dict):
                raise ValueError()
            rows, previous = [], ''
            with self.path.open() as stream:
                for line in iter(lambda: stream.readline(16385), ''):
                    if len(line) > 16384 or not line.endswith('\n'):
                        raise ValueError()
                    item = bounded_json(line)
                    if not isinstance(item, dict):
                        raise ValueError()
                    checksum = item.pop('hash')
                    if item.get('seq') != len(rows) or item.get('prev') != previous or checksum != digest(item):
                        raise ValueError()
                    if item.get('marker') != MARKER or item.get('kind') not in ('armed', 'attempt', 'clock'):
                        raise ValueError()
                    if any(type(item.get(k)) not in (int, float) or not 0 <= item[k] < 1e12 for k in ('at', 'mono')):
                        raise ValueError()
                    if rows and item['kind'] != 'clock':
                        self._clock_delta(rows[-1], item['at'], item['mono'])
                    if item['kind'] == 'attempt' and (set(item) != {'seq', 'prev', 'marker', 'kind', 'at', 'mono', 'session', 'turn', 'folders'} or not isinstance(item['folders'], list) or any(not isinstance(x, str) for x in item['folders']) or not isinstance(item['session'], str) or not isinstance(item['turn'], str)):
                        raise ValueError()
                    if item['kind'] != 'attempt' and set(item) != {'seq', 'prev', 'marker', 'kind', 'at', 'mono'}:
                        raise ValueError()
                    if (not rows) != (item['kind'] == 'armed'):
                        raise ValueError()
                    previous = checksum
                    rows.append(item)
            checkpoint = {'seq': len(rows)-1, 'hash': previous}
            with self.witness.open() as stream:
                witness = bounded_json(stream.read(16385))
            if self.witness.is_symlink() or witness != checkpoint:
                raise ValueError()
            if not rows or anchor != dict(checkpoint, witness=self._identity()):
                raise ValueError()
            return rows, previous
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise LedgerError('send-log-unarmed: run sidecarwatch rearm after checking local state') from exc
    def _identity(self):
        stat = self.witness.stat()
        return [stat.st_dev, stat.st_ino, stat.st_ctime_ns]
    @staticmethod
    def _clock_delta(row, wall, mono):
        elapsed, observed = mono-row['mono'], wall-row['at']
        if elapsed < 0 or observed < 0 or abs(elapsed-observed) > 5:
            raise LedgerError('send-log-clock: clock continuity needs explicit rearm')
    def _append(self, item, seq, previous):
        item = dict(item, mono=self.state.clock.monotonic(), seq=seq, prev=previous, marker=MARKER)
        checksum = digest(item)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(dict(item, hash=checksum)) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint = {'seq': seq, 'hash': checksum}
        write_private(self.witness, json.dumps(checkpoint))
        self.state.write('send-anchor.json', json.dumps(dict(checkpoint, witness=self._identity())))
    def arm(self, now):
        self.state.check()
        self.state.ensure()
        try:
            rows, previous = self.read()
        except LedgerError:
            suffix = secrets.token_hex(8)
            for path in (self.path, self.anchor, self.witness):
                if path.exists() or path.is_symlink():
                    os.replace(path, path.with_name(path.name + '.saved-' + suffix))
            self._append({'kind': 'armed', 'at': now.timestamp()}, 0, '')
            return
        try:
            self.attempts(now)
        except LedgerError:
            # Explicit clock recovery retains lifetime counts; old attempts count
            # as fresh for rate limits until a full new elapsed window has passed.
            self._append({'kind': 'clock', 'at': now.timestamp()}, len(rows), previous)
    def attempts(self, now):
        rows, _ = self.read()
        mono = self.state.clock.monotonic()
        self._clock_delta(rows[-1], now.timestamp(), mono)
        epoch = max(i for i, row in enumerate(rows) if row['kind'] in ('armed', 'clock'))
        self._clock_delta(rows[epoch], now.timestamp(), mono)
        result = []
        for i, row in enumerate(rows):
            if row['kind'] == 'attempt':
                baseline = rows[epoch] if i < epoch else row
                age = min(now.timestamp()-baseline['at'], mono-baseline['mono'])
                result.append(dict(row, at=now.timestamp()-age))
        return result
    def used(self, session, now):
        return any(row['session'] == session_key(session) and row['turn'] == session.turn_key for row in self.attempts(now))
    def reserve(self, session, now, maximum):
        rows, previous = self.read()
        attempts = self.attempts(now)
        own = [row for row in attempts if row['session'] == session_key(session)]
        if self.used(session, now) or max(len(own), session.auto_total) >= min(50, maximum):
            raise LedgerError('max-turns')
        if len([row for row in attempts if now.timestamp()-86400 < row['at']]) >= DAY_CAP:
            raise LedgerError('day-limit')
        self._append({'kind': 'attempt', 'at': now.timestamp(), 'session': session_key(session),
                      'turn': session.turn_key, 'folders': [digest(f) for f in sorted(session.folders)]}, len(rows), previous)
