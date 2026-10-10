from dataclasses import dataclass, field
import difflib, re
from . import LABEL
from .sessions import matches
from .turnstate import clean_completion
from .state import recent
from .ledger import LedgerError, session_key, digest, DAY_CAP
from .scrub import normalized

NEVER = {
 "money": [
  r"[$€£]\s?\d",
  r"\b\d+(\.\d+)?\s?(usd|eur|gbp|dollars?|euros?)\b",
  r"\b(pay|pays|paid|paying|payments?|purchas(e|es|ed|ing)|buy|buying|billing|billed|invoices?|refunds?|subscriptions?|subscribe|credit card|paid (tier|plan)|upgrade (the |your |my )?plan)\b",
 ],
 "credentials": [
  r"\b(passwords?|passphrases?|passcodes?|credentials?|api[ _-]?keys?|access[ _-]?keys?|secret keys?|client secrets?|private keys?|ssh keys?|(auth|access|bearer|refresh) tokens?|oauth|2fa|mfa|one[- ]time codes?)\b",
  r"\b(log ?in|sign ?in|authenticate) (to|with|as|using)\b",
  r"(^|[\s`'\"/])\.env(\.[a-z]+)?\b",
  r"\b(rotate|revoke|reset|share|paste|send|enter|provide) (the |your |my |a |an )?(key|token|secret|password)\b",
 ],
 "release": [
  r"\b(releas(e|es|ed|ing)|publish(es|ed|ing)?|deploy(s|ed|ing|ment)?|go(ing)? live|ship it|roll ?outs?)\b",
  r"\b(production|prod)\b",
  r"\bmerg(e|ed|ing) (it |this |that |the (pr|pull request|branch) )?(in)?to (main|master|trunk)\b",
  r"\b(force[- ]push|push --force|push -f)\b",
  r"\btag(ging)? (v?\d+\.\d+|a release|the release)\b",
  r"\b(npm publish|twine upload|cargo publish|gem push|docker push)\b",
  r"\bmak(e|ing) (it|this|the repo(sitory)?) public\b",
  r"\bchange (the )?visibility\b",
 ],
 "scope_change": [
  r"\b(also|additionally|in addition|while you(('| a)re| are) (at it|there)|on top of that),? (add|build|implement|create|write|refactor|rewrite|migrate|set up)\b",
  r"\b(new|extra|additional) (feature|requirement|endpoint|service|module|dependency|integration)\b",
  r"\b(expand|widen|extend|broaden|change|increase) (the )?scope\b",
  r"\b(instead|rather),? (build|implement|rewrite|switch to|migrate to|use)\b",
  r"\b(switch|migrate|move) (over )?to (a |an |the )?(different|new|another)\b",
  r"\b(drop|skip|remove|descope|cut) (the )?(requirement|feature|tests?|goal)\b",
 ],
 "decision": [
  r"\b(i|we|the user|your user|the owner) (approve[sd]?|authori[sz]e[sd]?|agree[sd]?|confirm(s|ed)?|decided?|chose|choose|pick(s|ed)?|prefer(s|red)?)\b",
  r"\b(approved|authori[sz]ed|permission granted|green light|sign(ed)? off|lgtm)\b",
  r"\byou have (my |our |the )?(permission|approval|go[- ]ahead)\b",
  r"\b(go with|choose|pick|select|use) (option|approach|plan|alternative|choice)\b",
  r"\boption [a-d1-9]\b",
  r"\b(yes|yep|yeah|ok|okay|sure),? (do|go|please|proceed|that works|sounds good)\b",
  r"\b(go ahead|proceed) (and|with)\b",
  r"\b(delete|remove|drop|wipe|reset|overwrite) (the |all |every )?(database|db|tables?|branch|repo|repository|data|files?|directory|folder|history)\b",
 ],
}

WORKER_ASKS = [
 r"\b(which|what) (one|option|approach|way|plan|alternative)\b",
 r"\b(do|would|did) you (want|prefer|like|mean)\b",
 r"\b(should|shall|can|may|could) (i|we)\b.*\bor\b",
 r"\b(please|kindly) (confirm|approve|decide|choose|advise|clarify)\b",
 r"\b(need|needs|require|requires|awaiting|waiting (for|on)) (your |the user'?s? |the owner'?s? )?(approval|decision|confirmation|input|permission|sign[- ]off|go[- ]ahead)\b",
 r"\b(option|approach|alternative) [a-d1-9]\b",
 r"\blet me know (which|how|if|whether|what)\b",
]
from .templates import TEMPLATES, CONTINUE, CLOSING, body as template_body, valid_body
WORKER_ASKS += [
    r"\b(should|shall|can|may|could) (i|we)\b",
    r"\b(tell me|reply|respond|confirm|approve|permission|confirmation|choose|decide)\b",
    r"\b(want me to|would you like|ready to|ok(?:ay)? to)\b",
]
UNSAFE_FINAL = r"\b(delet\w*|drop\w*|forc\w*|overwrit\w*|wip\w*|eras\w*|production|prod|payment\w*|credential\w*|push\w*|releas\w*|rebase|squash|error\w*|fail(?:ed|ure|ures)?|exception|traceback|blocked)\b"

def hits(text, patterns):
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

def never_check(reply, worker_final, overnight, *, skip_reply=False, extra_patterns=()):
    reply, worker_final = normalized(reply), normalized(worker_final)
    found = set()
    found.update(key for key in ('money','credentials','release','scope_change') if hits(worker_final, NEVER[key]))
    if not skip_reply:
        found.update(key for key, patterns in NEVER.items() if hits(reply, patterns))
        if hits(reply, extra_patterns):
            found.add('custom')
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', worker_final):
        asks = hits(sentence, WORKER_ASKS)
        if '?' not in sentence and not asks:
            continue
        categories = [key for key in ('money', 'credentials', 'release', 'scope_change') if hits(sentence, NEVER[key])]
        if categories:
            found.update(categories)
        elif asks:
            found.add('decision')
        else:
            found.add('decision')
        if hits(sentence, extra_patterns):
            found.add('custom')
    return sorted(found)

def similar(a, b):
    normal = lambda v: ' '.join(v.lower().split())
    return bool(a and b) and difflib.SequenceMatcher(None, normal(a), normal(b)).ratio() >= 0.90

@dataclass
class Options:
    auto_reply: bool = False
    allow: list = field(default_factory=list)
    overnight: bool = False
    live: bool = False
    max_turns: int = 5
    deadline: object = None
    sent: int = 0
    transcript_day_count: int = 0

@dataclass
class Decision:
    reason: str
    body: str
    needs_user: bool = False

def decide(session, verdict, state, cfg, options, now, *, fresh=None, available=False):
    v = verdict or {}
    continuing = v.get('template') == 'continue'
    body = template_body(v.get('template'), getattr(verdict, 'profile', None), v.get('decided_index', -1))
    def fail(reason, need=False):
        return Decision(reason, body, need)
    if not options.auto_reply:
        return fail('auto-reply-off')
    if state.stopped():
        return fail('stopped')
    if not any(matches(session, p) for p in options.allow):
        return fail('not-allowed')
    if session.subagent or session.state != 'idle' or not session.turns_finished:
        return fail('not-idle')
    if not valid_session_id(session.id):
        return fail('invalid-session-id', True)
    if session.parse_limited:
        return fail('incomplete-transcript', True)
    if state.handled(session):
        return fail('already-handled')
    if not verdict:
        return fail('invalid-verdict')
    if getattr(verdict, 'reason', ''):
        return fail(verdict.reason, True)
    if v['kind'] != '1' or v['needs_user_topics']:
        return fail('needs-user', bool(body) or v['kind'] == '2')
    if v['template'] not in TEMPLATES:
        return fail('inconsistent-verdict', bool(body))
    if v['question_for_user'] or v['label'] in ('NEEDS_USER', 'BLOCKED', 'UNSURE'):
        return fail('needs-user', True)
    if v['confidence'] == 'low':
        return fail('low-confidence', bool(body))
    if session.auto_turns and v['worker_pushed_back']:
        return fail('pushback', True)
    if continuing and not options.overnight:
        return fail('continue-not-enabled', bool(body))
    categories = never_check(body, session.final_message, options.overnight, extra_patterns=cfg['never']['extra_patterns'])
    if categories:
        return fail('never:' + categories[0], bool(body))
    if not session.final_message.strip() or hits(session.final_message, [UNSAFE_FINAL]) or any(hits(session.final_message, NEVER[k]) for k in ('money', 'credentials', 'release', 'scope_change')):
        return fail('unsafe-worker-message', True)
    if not clean_completion(session):
        return fail('completion-not-established', True)
    attempts = []
    if options.live:
        try:
            attempts = state.send_log.attempts(now)
        except LedgerError as exc:
            return fail(str(exc).split(':')[0], True)
    own = [row for row in attempts if row['session'] == session_key(session)]
    if any(row['turn'] == session.turn_key for row in own):
        return fail('already-attempted', True)
    if max(session.auto_turns, session.auto_total, len(own)) >= min(options.max_turns, 50):
        return fail('max-turns', True)
    if max(options.transcript_day_count, len([row for row in attempts if now.timestamp()-86400 < row['at']])) >= DAY_CAP:
        return fail('day-limit', True)
    if not continuing and any(similar(body, old) for old in session.auto_recent):
        return fail('repeat-reply', True)
    if session.auto_turns >= 1 and similar(session.final_message, session.previous_final):
        return fail('no-progress', True)
    stamps = recent(state.session(session)['sends'], now, 3600)
    recorded = [row['at'] for row in own if now.timestamp()-3600 < row['at']]
    if len(recorded) > len(stamps):
        stamps = recorded
    limits = cfg['auto_reply']
    if stamps and now.timestamp() - max(stamps) < limits['min_gap_seconds']:
        return fail('cooldown', True)
    if len(stamps) >= limits['max_per_session_per_hour']:
        return fail('rate-limit', True)
    if any(max(len(recent(state.data['folders'].get(f, []), now, 86400)), sum(now.timestamp()-86400 < row['at'] and digest(f) in row['folders'] for row in attempts)) >= limits['max_per_folder_per_day'] for f in session.folders):
        return fail('folder-limit', True)
    if options.sent >= limits['max_per_run']:
        return fail('run-budget', True)
    if options.deadline and now >= options.deadline:
        return fail('deadline', True)
    current = fresh() if fresh else None
    if current is None or not clean_completion(current) or current.subagent or current.parse_limited or current.cwd != session.cwd or current.folders != session.folders or current.id != session.id or current.turn_key != session.turn_key or current.evidence_key != session.evidence_key or current.state != 'idle' or current.latest_instruction != session.latest_instruction or current.auto_turns != session.auto_turns:
        return fail('stale')
    if not options.live:
        return fail('dry-run')
    if not available:
        return fail('no-injection')
    return Decision('send', body)

def label(session, body, reviewer, maximum, count=None):
    text = f'{LABEL}{(max(session.auto_turns, session.auto_total) if count is None else count) + 1}/{maximum} | written by an AI reviewer ({reviewer}), not by the user]\n{body}\n{CLOSING}'
    if not valid_body(body) or reviewer not in ('claude', 'codex'):
        raise ValueError('invalid automatic message')
    return text

def valid_session_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value) is not None

def valid_message(text):
    if not isinstance(text, str) or len(text) > 1000 or not text.isascii():
        return False
    parts = text.splitlines()
    if len(parts) != 3 or not valid_body(parts[1]) or parts[2] != CLOSING:
        return False
    match = re.fullmatch(re.escape(LABEL) + r'([0-9]{1,2})/([0-9]{1,2}) \| written by an AI reviewer \((claude|codex)\), not by the user\]', parts[0])
    return bool(match and 1 <= int(match[1]) <= int(match[2]) <= 50)
