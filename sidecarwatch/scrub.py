"""Conservative text scrubbing; uncertain structured values are withheld whole."""
import json, re, unicodedata
from pathlib import Path

MAX_TEXT = 262144
MAX_DEPTH = 32
DEFAULT = {'emails': True, 'extra_patterns': [], 'extra_terms': []}
KEY_PARTS = ('password', 'passwd', 'passphrase', 'passcode', 'secret', 'token',
             'apikey', 'accesskey', 'privatekey', 'signingkey', 'credential',
             'authorization', 'cookie', 'connectionstring')
SCRUB = [
 ('private-key', r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)', 0),
 ('aws-key', r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b', 0),
 ('github-token', r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+', 0),
 ('api-key', r'\bsk-[A-Za-z0-9_-]+', 0),
 ('slack-token', r'\bxox[baprs]-[A-Za-z0-9-]+', 0),
 ('google-key', r'\bAIza[0-9A-Za-z_-]+', 0),
 ('jwt', r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', 0),
 ('bearer', r'\bbearer\s+[A-Za-z0-9._~+/=-]+', re.IGNORECASE),
 ('basic-auth', r'\bbasic\s+[A-Za-z0-9+/=_-]+', re.IGNORECASE),
 ('token', r'\b(?:[rs]k_(?:live|test)_|glpat-|npm_|pypi-|hf_)[A-Za-z0-9_-]+', 0),
 ('hex', r'\b[0-9a-fA-F]{32,}\b', 0),
 ('opaque', r'(?<![A-Za-z0-9_+/-])[A-Za-z0-9_+/-]{32,}={0,2}(?![A-Za-z0-9_+/=-])', 0),
]
EMAIL = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
SSN = r'(?<![0-9])[0-9]{3}[- ][0-9]{2}[- ][0-9]{4}(?![0-9])'
CARD = r'(?<![0-9A-Za-z-])[0-9](?:[ -]?[0-9]){12,18}(?![ -]?[0-9])'
IBAN = r'\b[A-Za-z]{2}[0-9]{2}(?:[A-Za-z0-9]{11,30}|(?:[ -][A-Za-z0-9]{4}){2,7}(?:[ -][A-Za-z0-9]{1,4})?)\b'
ASSIGNMENT = re.compile(r'''(?<![\w.-])(?P<key>["']?[\w.-]+["']?)\s*(?:[:=]|\bis\b|\bare\b)\s*''', re.I)

class ScrubUncertain(ValueError):
    def __init__(self, kind):
        super().__init__('scrub-uncertain: deliberate privacy refusal. Nothing was sent. '
                         'Remove sensitive material at its source before reviewing again; '
                         'use show for local inspection. Triggered rule class: ' + kind)
        self.kind = kind

def luhn(number):
    digits = [int(char) for char in number]
    return sum(digit if index % 2 == 0 else (digit * 2 - 9 if digit > 4 else digit * 2)
               for index, digit in enumerate(reversed(digits))) % 10 == 0

def personal_data(text):
    # Normalize before matching so full-width digits and invisible separators
    # cannot bypass these patterns. Grouped cards are withheld even with a typo.
    text = normalized(text)
    def iban(match):
        value = match[0]
        return '[REDACTED:iban]' if 15 <= len(value.replace(' ', '').replace('-', '')) <= 34 else value
    text = re.sub(IBAN, iban, text)
    def card(match):
        value = match[0]
        number = re.sub(r'[^0-9]', '', value)
        return '[REDACTED:card]' if ' ' in value or '-' in value or luhn(number) else value
    text = re.sub(CARD, card, text)
    return re.sub(SSN, '[REDACTED:ssn]', text)

def normalized(text):
    text = unicodedata.normalize('NFKC', text)
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf').translate(
        str.maketrans('аесорхуіјѕΑΒΕΖΗΙΚΜΝΟΡΤΥΧ', 'aecopxyijsABEZHIKMNOPTYX'))

def sensitive_key(key):
    key = normalized(key).lower()
    # Unrecognized alphabets in a structured key cannot classify its value safely.
    if not key.isascii():
        return True
    key = re.sub(r'[^a-z0-9]', '', key)
    return key in ('key', 'auth', 'pwd', 'sessionid') or any(part in key for part in KEY_PARTS)

def validate_strings(value):
    if isinstance(value, str):
        if len(value) > MAX_TEXT or '\x00' in value or '\ufffd' in value:
            raise ValueError('malformed or oversized string')
        try:
            value.encode('utf-8')
        except UnicodeError as exc:
            raise ValueError('invalid Unicode string') from exc
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_strings(key)
            validate_strings(item)
    elif isinstance(value, list):
        for item in value:
            validate_strings(item)

def bounded_json(text):
    """Reject deep structures before invoking the JSON decoder."""
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError('JSON depth limit')
        elif char in ']}':
            depth -= 1
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique)
    validate_strings(value)
    return value

def _structure(value, cfg, depth):
    if depth > MAX_DEPTH:
        raise ScrubUncertain('structured')
    if isinstance(value, dict):
        if any(sensitive_key(k) for k in value):
            raise ScrubUncertain('assignment')
        return {_scrub(str(k), cfg, depth+1): _structure(v, cfg, depth+1) for k, v in value.items()}
    if isinstance(value, list):
        return [_structure(v, cfg, depth+1) for v in value]
    if isinstance(value, str):
        return _scrub(value, cfg, depth+1)
    if type(value) is int:
        clean = _scrub(str(value), cfg, depth+1)
        return clean if clean != str(value) else value
    return value

def without_frames(text):
    from . import LABEL
    from .policy import valid_message
    pattern = re.escape(LABEL) + r'[^\r\n]*\n[^\r\n]*\n[^\r\n]*'
    return re.sub(pattern, lambda m: '' if valid_message(m[0]) else m[0], text)

def labelled_value(text):
    # Both views matter: comments can decorate a label or split its letters.
    original = normalized(without_frames(text)).lower()
    original = re.sub(r'\[redacted:[a-z-]+\]', '', original)
    undecorated = re.sub(r'[`*_#|<>]', ' ', original)
    views = (original, undecorated, re.sub(r'/\*[\s\S]*?\*/|<!--[\s\S]*?-->', '', original))
    for match in re.finditer(r'(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|<)([^\n:=>]+)(?:[\n:=>])([\s\S]+)', original):
        if sensitive_key(match[1]) and match[2].strip():
            return True
    labels = (*KEY_PARTS, 'key', 'auth', 'pwd', 'sessionid')
    for scan in views:
        scan = re.sub(r'\\\r?\n', '', scan)
        for label in labels:
            pattern = r'(?<![a-z])' + r'[\W_]*'.join(label) + r'(?:[a-z0-9_]*)(?![a-z])'
            for match in re.finditer(pattern, scan):
                rest = scan[match.end():].strip(' \t\r\n`*_:=|<>/-\"\'')
                if rest and not re.fullmatch(r'\[redacted:[a-z-]+\]', rest):
                    return True
        for match in ASSIGNMENT.finditer(scan):
            rest = scan[match.end():].strip()
            if sensitive_key(match['key'].strip('"\'')) and rest and not re.fullmatch(r'\[redacted:[a-z-]+\]', rest):
                return True
    return False

def review_check(texts, cfg):
    from .policy import valid_message
    parts = [text for text in texts if not valid_message(text.strip())]
    # This check precedes every cap, including events outside the recent window.
    for part in parts:
        scrub(part, cfg, strict=True)
    if labelled_value('\n'.join(parts)):
        raise ScrubUncertain('assignment')

def _scrub(text, cfg, depth=0):
    if depth > MAX_DEPTH or len(text) > MAX_TEXT:
        raise ScrubUncertain('oversized')
    validate_strings(text)
    trimmed = text.strip()
    if re.fullmatch(r'\[REDACTED:[a-z-]+\]', trimmed):
        return text
    from . import LABEL
    if trimmed.startswith(LABEL):
        from .policy import valid_message
        if valid_message(trimmed):
            return trimmed
    if trimmed.startswith(('{', '[')):
        try:
            value = bounded_json(trimmed)
        except (ValueError, RecursionError) as exc:
            raise ScrubUncertain('structured') from exc
        return json.dumps(_structure(value, cfg, depth+1), ensure_ascii=True)
    # Tool summaries have a name followed by a complete JSON argument container.
    embedded = re.fullmatch(r'([A-Za-z_][\w.-]*: )([\[{][\s\S]*)', text)
    if embedded:
        return embedded[1] + _scrub(embedded[2], cfg, depth+1)
    if re.search(r'\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|["\\])', text):
        raise ScrubUncertain('encoded')
    text = text.replace(str(Path.home()), '~')
    scan = normalized(text)
    # A free-form value has no trustworthy terminator: whitespace, punctuation,
    # comments, masking, and following lines may all belong to the same secret.
    for match in ASSIGNMENT.finditer(scan):
        key = match['key'].strip('"\'')
        env_key = re.fullmatch(r'[A-Z_][A-Z0-9_]*', key) and '=' in match[0]
        if sensitive_key(key) or env_key:
            if re.fullmatch(r'\[REDACTED:[a-z-]+\]', scan[match.end():].strip()):
                continue
            raise ScrubUncertain('assignment')
    if re.search(r'(?i)(?<!\S)(?:--(?:password|passwd|passphrase|token|secret|api-key|user)(?:=|\s)|-p(?=\S)|-u\s)', scan):
        raise ScrubUncertain('argument')
    folder_spans = []
    for path in re.finditer(r'(?<![\w/])/(?:[A-Za-z0-9_.+~-]+/)*[A-Za-z0-9_.+~-]*/?', scan):
        if all(len(component) < 32 for component in path[0].split('/')) and Path(path[0]).is_dir():
            folder_spans.append(path.span())
    for kind, pattern, flags in SCRUB:
        for match in re.finditer(pattern, scan, flags=flags):
            # Slash-separated ordinary path components are not one opaque
            # value. Still inspect every component, including token prefixes
            # and long opaque/hex names; do not exempt an entire path.
            if kind == 'opaque':
                if any(start <= match.start() and match.end() <= end for start, end in folder_spans):
                    continue
            if kind == 'opaque' and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', match[0]):
                continue
            raise ScrubUncertain(kind)
    # Include masked tokens whose only recognizable part is their prefix.
    if re.search(r'\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|glpat-|hf_|npm_)', scan):
        raise ScrubUncertain('token')
    if re.search(r'://[^/\s<>"\']+@', scan):
        raise ScrubUncertain('url-login')
    if labelled_value(text):
        raise ScrubUncertain('assignment')
    text = personal_data(text)
    if cfg['emails']:
        text = re.sub(EMAIL, '[REDACTED:email]', text)
    for pattern in cfg['extra_patterns']:
        text = re.sub(pattern, '[REDACTED:custom]', text)
    for term in cfg['extra_terms']:
        if term:
            text = text.replace(term, '[REDACTED:custom]')
    return text.replace(str(Path.home()), '~')

def scrub(text, cfg=None, *, strict=False):
    try:
        return _scrub(text, cfg or DEFAULT)
    except (ValueError, TypeError, RecursionError, re.error) as exc:
        kind = exc.kind if isinstance(exc, ScrubUncertain) else 'scrub-failed'
        if strict:
            raise ScrubUncertain(kind) from exc
        return '[REDACTED:'+kind+']'

def display_scrub(text, cfg=None):
    """Scrub each message independently; retain safe leading lines.

    An uncertain multiline value has no reliable terminator, so its remaining
    span in this message stays withheld. It never blanks other messages.
    Strict outbound checks still scan the complete transcript before any cap.
    """
    cfg = cfg or DEFAULT
    try:
        return scrub(text, cfg, strict=True)
    except ScrubUncertain as exc:
        if text.lstrip().startswith(('{', '[')) or exc.kind in ('structured', 'encoded', 'oversized', 'scrub-failed'):
            return '[REDACTED:'+exc.kind+']'
        if exc.kind == 'github-token':
            # A recognized complete token has a defined span in ordinary
            # prose. Assignment/structured values still use the conservative
            # whole-value handling below; outbound review remains refused.
            clean = normalized(text)
            for kind, pattern, flags in SCRUB:
                clean = re.sub(pattern, '[REDACTED:'+kind+']', clean, flags=flags)
            clean = personal_data(clean)
            if cfg['emails']:
                clean = re.sub(EMAIL, '[REDACTED:email]', clean)
            for pattern in cfg['extra_patterns']:
                clean = re.sub(pattern, '[REDACTED:custom]', clean)
            for term in cfg['extra_terms']:
                if term:
                    clean = clean.replace(term, '[REDACTED:custom]')
            # Anything uncertain outside the matched spans remains withheld.
            remainder = re.sub(r'(?i)\btoken\s+\[REDACTED:github-token\]', '', clean)
            remainder = re.sub(r'\[REDACTED:[a-z-]+\]', '', remainder)
            try:
                scrub(remainder, cfg, strict=True)
            except ScrubUncertain as remaining:
                return '[REDACTED:'+remaining.kind+']'
            return clean.replace(str(Path.home()), '~')
        clean = []
        for line in text.splitlines():
            if label_only(line):
                break
            try:
                clean.append(scrub(line, cfg, strict=True))
            except ScrubUncertain:
                break
        else:
            return '[REDACTED:'+exc.kind+']'
        return '\n'.join(clean + ['[REDACTED:'+exc.kind+']'])

def label_only(text):
    if '\n' in text.strip() or '\r' in text.strip():
        return False
    value = re.sub(r'[`*_#|<>]', '', normalized(text)).strip().rstrip(':=-→').strip()
    identifier = bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', value))
    value = re.sub(r'[\s_.-]', '', value).lower()
    # Only actual labels carry a value into the next message. A sentence
    # mentioning a label (and its already-redacted value) is not a label.
    labels = (*KEY_PARTS, 'key', 'auth', 'pwd', 'sessionid')
    return value in labels or identifier and sensitive_key(value)
