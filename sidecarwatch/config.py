import datetime as dt
import os
from pathlib import Path
import re, tomllib
from . import APP_NAME, PREFIX

DEFAULTS = {
    'privacy': {'allow_cross_vendor_review': False},
    'discovery': {'window_hours': 24, 'quiet_seconds': 60},
    'watch': {'only': []},
    'reviewer': {'timeout_seconds': 300, 'max_per_hour': 20, 'max_per_run': 100,
                 'claude': {'command': 'claude', 'model': '', 'extra_args': []},
                 'codex': {'command': 'codex', 'model': '', 'extra_args': []}},
    'digest': {'max_chars': 12000, 'recent_events': 15},
    'auto_reply': {'max_turns': 5, 'min_gap_seconds': 120, 'max_per_session_per_hour': 6,
                   'max_per_folder_per_day': 30, 'max_per_run': 40},
    'delivery': {'codex_queue': False, 'claude_resume': False, 'turn_timeout_seconds': 1800,
                 'claude': {'extra_args': []}},
    'scrub': {'emails': True, 'extra_patterns': [], 'extra_terms': []},
    'never': {'extra_patterns': []}, 'log': {'text': 'verdict', 'max_bytes': 5000000},
}
FORBIDDEN_ARGS = ('--bare', '--yolo', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
                  'bypassPermissions', '--dangerously-bypass-approvals-and-sandbox',
                  '--dangerously-bypass-hook-trust', 'danger-full-access')
LIMITS = {'reviewer.timeout_seconds': (1, 600), 'delivery.turn_timeout_seconds': (1, 3600),
          'reviewer.max_per_hour': (1, 60), 'reviewer.max_per_run': (1, 500),
          'digest.max_chars': (512, 40000), 'auto_reply.max_turns': (1, 50),
          'auto_reply.min_gap_seconds': (30, None), 'auto_reply.max_per_session_per_hour': (1, 20),
          'auto_reply.max_per_folder_per_day': (1, 100), 'auto_reply.max_per_run': (1, 200)}

class ConfigError(ValueError):
    pass

def merge(base, values, prefix=''):
    if not isinstance(values, dict):
        raise ConfigError(prefix + ': expected table')
    for key in values:
        if key not in base:
            raise ConfigError(prefix + key + ': unknown key')
    result = {}
    for key, default in base.items():
        name = prefix + key
        val = values.get(key, default)
        if isinstance(default, dict):
            result[key] = merge(default, val, name + '.')
            continue
        if type(val) is not type(default) or isinstance(val, list) and any(not isinstance(x, str) for x in val):
            raise ConfigError(name + ': wrong type')
        if isinstance(val, int) and not isinstance(val, bool):
            lo, hi = LIMITS.get(name, (1, None))
            if val < lo or hi is not None and val > hi:
                raise ConfigError(name + ': outside permitted limits')
        if key == 'extra_args' and any(bad.lower() in arg.lower() for bad in FORBIDDEN_ARGS for arg in val):
            raise ConfigError(name + ': forbidden argument')
        if key == 'extra_args' and val:
            if name.startswith('reviewer.') or name == 'delivery.claude.extra_args' and val not in (["--permission-mode", "default"], ["--permission-mode", "acceptEdits"], ["--permission-mode", "plan"]):
                raise ConfigError(name + ': unsupported arguments; reviewer arguments are fixed')
        if key == 'model' and val and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', val):
            raise ConfigError(name + ': invalid model name')
        if key == 'extra_patterns':
            try:
                for pattern in val:
                    re.compile(pattern)
            except re.error as exc:
                raise ConfigError(name + ': invalid pattern') from exc
        result[key] = list(val) if isinstance(val, list) else val
    return result

def directories(args):
    home = Path.home()
    config_dir = Path(os.environ.get(PREFIX + 'CONFIG_DIR', Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')) / APP_NAME)).expanduser()
    state_dir = Path(os.environ.get(PREFIX + 'STATE_DIR', Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')) / APP_NAME)).expanduser()
    cfg_path = Path(args.config).expanduser() if getattr(args, 'config', None) else config_dir / 'config.toml'
    homes = {}
    for agent, var in [('claude', 'CLAUDE_CONFIG_DIR'), ('codex', 'CODEX_HOME')]:
        homes[agent] = Path(getattr(args, agent + '_home', None) or os.environ.get(PREFIX + agent.upper() + '_HOME') or os.environ.get(var) or home / ('.' + agent)).expanduser()
    return cfg_path, config_dir, state_dir, homes

def read_toml(path):
    try:
        with path.open('rb') as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        # Parser messages can quote input values; report location only.
        location = re.search(r'\(at (line \d+, column \d+|end of document)\)', str(exc))
        raise ConfigError('invalid TOML: ' + str(path) + (' (' + location[1] + ')' if location else '')) from exc
    except OSError as exc:
        raise ConfigError('cannot read config: ' + str(path)) from exc

def remember_consent(path):
    """Change only the explicit privacy choice in the user's selected config."""
    from .storage import write_private
    text = path.read_text() if path.exists() else ''
    section = re.search(r'(?m)^\[privacy\]\s*(?:#[^\n]*)?$', text)
    if section:
        end = re.search(r'(?m)^\[', text[section.end():])
        stop = section.end() + end.start() if end else len(text)
        body = text[section.end():stop]
        choice = re.compile(r'(?m)^(\s*allow_cross_vendor_review\s*=\s*)(?:false|true)\b')
        body = choice.sub(r'\g<1>true', body) if choice.search(body) else '\nallow_cross_vendor_review = true\n' + body
        text = text[:section.end()] + body + text[stop:]
    else:
        # Dotted/inline TOML tables cannot be safely rewritten as another table.
        if path.exists() and 'privacy' in read_toml(path):
            raise ConfigError('set privacy.allow_cross_vendor_review=true in your config, or use a [privacy] table')
        text += '\n[privacy]\nallow_cross_vendor_review = true\n'
    merge(DEFAULTS, tomllib.loads(text))
    write_private(path, text)

def load(path, required=False):
    try:
        result = merge(DEFAULTS, read_toml(path) if required or path.exists() else {})
        if result['log']['text'] not in ('none', 'verdict', 'digest'):
            raise ConfigError('log.text: expected none, verdict or digest')
    except ConfigError as exc:
        raise ConfigError(str(path) + ': ' + str(exc)) from exc
    return result

def duration(value):
    if not re.fullmatch(r'\d+(s|m|h)', value):
        raise ConfigError('invalid duration: ' + value)
    return int(value[:-1]) * {'s': 1, 'm': 60, 'h': 3600}[value[-1]]

def when(value, now, future=False):
    if re.fullmatch(r'\d+(s|m|h)', value):
        return now - dt.timedelta(seconds=duration(value))
    try:
        if re.fullmatch(r'\d{2}:\d{2}', value):
            h, m = map(int, value.split(':'))
            result = now.astimezone().replace(hour=h, minute=m, second=0, microsecond=0)
            return result + dt.timedelta(days=int(future and result <= now))
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone() if result.tzinfo is None else result
    except ValueError as exc:
        raise ConfigError('invalid time: ' + value) from exc
