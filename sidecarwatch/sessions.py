from dataclasses import dataclass, field
import datetime as dt
import hashlib, os
from pathlib import Path
import subprocess
from . import APP_NAME, LABEL, PREFIX
from .config import ConfigError
from .scrub import bounded_json
from .markers import observe

@dataclass
class Session:
    agent: str
    id: str
    path: Path
    name: str = ''
    cwd: str = ''
    folders: set = field(default_factory=set)
    state: str = 'unknown'
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    turns_finished: int = 0
    last_finished_at: dt.datetime | None = None
    first_request: str = ''
    latest_instruction: str = ''
    final_message: str = ''
    previous_final: str = ''
    activity: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    auto_turns: int = 0
    auto_total: int = 0
    auto_stamps: list = field(default_factory=list)
    auto_recent: list = field(default_factory=list)
    marker_sources: dict = field(default_factory=dict, repr=False)
    unmatched_marker_stamps: list = field(default_factory=list, repr=False)
    unknown_lines: int = 0
    total_lines: int = 0
    shapes: dict = field(default_factory=dict)
    subagent: bool = False
    parse_limited: bool = False
    evidence: object = field(default=None, repr=False)
    evidence_key: str = ''
    parse_notes: list = field(default_factory=list)
    completion_confirmed: bool = False
    turn_failed: bool = False
    @property
    def turn_key(self):
        return f'{self.turns_finished}:{hashlib.sha256(self.final_message.encode("utf-8", errors="backslashreplace")).hexdigest()[:12]}'
    def event(self, at, role, text):
        if text:
            self.activity.append((at, role, text))
            self.messages.append((at, role, text))
    def user(self, at, text):
        self.activity = []
        self.completion_confirmed = False
        self.turn_failed = False
        if LABEL in text:
            self.auto_turns += 1
            self.auto_recent = (self.auto_recent + ['\n'.join(text.splitlines()[1:-1])])[-2:]
            role = 'auto'
        else:
            self.first_request = self.first_request or text
            self.latest_instruction = text
            self.auto_turns = 0
            self.auto_recent = []
            role = 'user'
        self.event(at, role, text)
        self.state = 'running'
    def finish(self, at, text, confirmed=False):
        self.completion_confirmed = confirmed
        self.turns_finished += 1
        self.last_finished_at = at
        self.previous_final, self.final_message = self.final_message, text
        self.state = 'idle'

def obj(value):
    return value if isinstance(value, dict) else {}

def string(value):
    return value if isinstance(value, str) else ''

def content(value):
    if isinstance(value, str):
        return value
    return '\n'.join(string(obj(x).get('text')) for x in value) if isinstance(value, list) else ''

def stamp(value, fallback):
    try:
        result = dt.datetime.fromisoformat(string(value).replace('Z', '+00:00'))
        return result.astimezone() if result.tzinfo is None else result
    except ValueError:
        return fallback

def lines(path, session=None):
    fingerprint = hashlib.sha256()
    with path.open(encoding='utf-8', errors='replace') as f:
        while True:
            line = f.readline(1048577)
            if not line:
                break
            if session:
                fingerprint.update(line.encode('utf-8', errors='backslashreplace'))
                session.evidence_key = fingerprint.hexdigest()
            oversized = len(line) > 1048576
            if oversized:
                while line and not line.endswith('\n'):
                    line = f.readline(1048577)
            if session:
                session.total_lines += 1
            try:
                if oversized:
                    raise ValueError('line size limit')
                value = bounded_json(line)
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, RecursionError) as exc:
                if session:
                    session.unknown_lines += 1
                    session.parse_limited = True
                    note = f'line {session.total_lines}: skipped malformed or oversized JSON/string'
                    if len(session.parse_notes) < 10:
                        session.parse_notes.append(note)
                continue
            if session:
                payload = obj(value.get('payload'))
                shape = '/'.join(string(x) for x in (value.get('type'), payload.get('type'), obj(payload.get('item')).get('type')) if isinstance(x, str)) or '(missing)'
                source = shape + '/' + string(payload.get('role'))
                observe(session, value, source, stamp(value.get('timestamp'), session.updated_at).timestamp())
                session.shapes[shape] = session.shapes.get(shape, 0) + 1
            yield value

def folders(cwd, cache):
    if not isinstance(cwd, str) or not cwd.strip() or not Path(cwd).is_absolute():
        return set()
    if cwd not in cache:
        try:
            path = Path(cwd).resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            return set()
        if not path.is_dir():
            return set()
        result = {str(path)}
        if path.is_dir():
            try:
                run = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir'], env={**{k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0', PREFIX + 'ACTIVE': '1'}, capture_output=True, text=True, timeout=5)
                if run.returncode == 0:
                    result.add(str(Path(run.stdout.strip()).resolve().parent))
            except (OSError, subprocess.TimeoutExpired):
                pass
        cache[cwd] = result
    return cache[cwd]

def inside(path, parent):
    if not str(path).strip() or not str(parent).strip() or not Path(path).is_absolute():
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(parent).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        return False

def matches(session, path):
    return any(inside(folder, path) for folder in session.folders)

def discover(homes, now, cfg, state_dir, notes=None):
    from .claude_format import parse as claude_parse
    from .codex_format import parse as codex_parse
    result, cache, names = [], {}, {}
    index = homes['codex'] / 'session_index.jsonl'
    index_state = Session('codex', 'index', index)
    if index.is_file():
        try:
            for row in lines(index, index_state):
                if isinstance(row.get('id'), str) and isinstance(row.get('thread_name'), str):
                    names[row['id']] = row['thread_name']
        except OSError:
            pass
    if notes is not None and index_state.parse_notes:
        notes.append(str(index)+': '+index_state.parse_notes[0])
    for agent, paths, parser in [('claude', homes['claude'].glob('projects/*/*.jsonl'), claude_parse), ('codex', (homes['codex'] / 'sessions').rglob('rollout-*.jsonl'), codex_parse)]:
        for path in paths:
            try:
                if now.timestamp() - path.stat().st_mtime > cfg['window_hours'] * 3600:
                    continue
                session = parser(path, now, cfg['quiet_seconds'])
            except OSError:
                continue
            if session.subagent or session.cwd and inside(session.cwd, state_dir) or session.first_request.startswith(APP_NAME.upper() + '-REVIEW-REQUEST'):
                continue
            if notes is not None and session.parse_notes:
                notes.append(str(path)+': '+session.parse_notes[0])
            session.folders = folders(session.cwd, cache)
            session.name = names.get(session.id, '')
            result.append(session)
    return sorted(result, key=lambda s: s.updated_at, reverse=True)

def scope(items, only, profiles):
    from .profiles import applies
    tests = []
    for value in only:
        profile = next((p for p in profiles if p['name'] == value), None)
        if profile:
            tests.append(lambda s, p=profile: applies(s, p))
        elif value.strip() and Path(value).expanduser().is_dir():
            tests.append(lambda s, p=value: matches(s, p))
        else:
            raise ConfigError('unknown scope: ' + value + '; use an existing folder or a configured profile name')
    return [s for s in items if not tests or any(test(s) for test in tests)]

def target(items, value=None, cwd=None):
    if value in ('claude', 'codex'):
        found = [s for s in items if s.agent == value][:1]
    elif value:
        exact = [s for s in items if s.id == value or s.agent == 'codex' and s.name == value]
        found = exact or [s for s in items if len(value) >= 6 and s.id.startswith(value)]
    else:
        local = Session('', '', Path('.'), folders=folders(str(cwd or Path.cwd()), {}))
        found = [s for s in items if any(matches(s, f) or matches(local, p) for f in local.folders for p in s.folders)][:1]
    if len(found) != 1:
        raise LookupError('ambiguous: ' + ', '.join(s.agent + ' ' + s.id for s in found) if found else 'no matching session; run sessions and use an exact ID/name or a prefix of at least six characters')
    return found[0]

def branch(cwd):
    if not cwd or not Path(cwd).is_dir():
        return '-'
    try:
        env = {k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null', GIT_OPTIONAL_LOCKS='0')
        result = subprocess.run(['git','-C',cwd,'branch','--show-current'], env=env, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or '-'
    except (OSError, subprocess.TimeoutExpired):
        return '-'
