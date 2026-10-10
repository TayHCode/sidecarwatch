from pathlib import Path
import hashlib, re
from .config import ConfigError, read_toml, merge
from .sessions import matches

FIELDS = {'name': '', 'paths': [], 'match': [], 'goal': '', 'in_scope': [], 'out_of_scope': [], 'decided': [], 'ask_me': [], 'rules': [], 'auto_reply': {'allow': False}}
DEFAULT = {**FIELDS, 'name': 'default', 'goal': "(none written) Judge against the user's first request and latest instruction.", 'rules': ["Follow the repository's own AGENTS.md; current origin/main wins.", 'KISS: smallest safe path; reuse before building; no new infrastructure or orchestration machinery without a real need.', 'Work in worktrees, never the canonical checkout or /tmp.', 'No destructive or production changes, account access, payments or external messages without the owner.'], 'ask_me': ['Anything consequential, irreversible or outward-facing.']}

def load(folder, explicit=None):
    paths = [Path(explicit).expanduser()] if explicit else sorted((folder / 'profiles').glob('*.toml'))
    result = []
    for path in paths:
        profile = merge(FIELDS, read_toml(path))
        if not re.fullmatch('[a-z0-9-]+', profile['name']):
            raise ConfigError('profile.name: required lowercase name')
        if any(not value.strip() for value in profile['paths'] + profile['match']):
            raise ConfigError('profile paths and match entries must be nonempty')
        from .templates import valid_quote
        profile['_quotable'] = [valid_quote(v) for v in profile['decided']]
        profile['_source'] = str(path)
        profile['_digest'] = hashlib.sha256(path.read_bytes()).hexdigest()
        result.append(profile)
    return result

def applies(session, profile):
    return any(matches(session, p) for p in profile['paths']) or any(v.lower() in (session.name + ' ' + session.cwd).lower() for v in profile['match'])

def match(session, profiles):
    ranked = [(len(str(Path(p).expanduser().resolve())), -i, profile) for i, profile in enumerate(profiles) for p in profile['paths'] if matches(session, p)]
    if ranked:
        return max(ranked, key=lambda row: row[:2])[2]
    return next((p for p in profiles if any(v.lower() in (session.name + ' ' + session.cwd).lower() for v in p['match'])), DEFAULT)

def allowed_paths(profiles):
    return [path for p in profiles if p['auto_reply']['allow'] for path in p['paths']]

def text(profile):
    return '\n'.join(f'{key}: ' + ('; '.join(value) if isinstance(value, list) else str(value)) for key, value in profile.items() if key not in ('auto_reply', 'paths', 'match') and not key.startswith('_'))

def unchanged(profile):
    if '_source' not in profile:
        return profile is DEFAULT
    try:
        return hashlib.sha256(Path(profile['_source']).read_bytes()).hexdigest() == profile['_digest']
    except OSError:
        return False
