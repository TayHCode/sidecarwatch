"""Structured turn evidence authorizes eligibility; text can only veto it."""
import datetime as dt
import re
from .scrub import normalized

BAD = re.compile(r'(?i)\b(?:\w*error\w*|fail\w*|fatal|abort\w*|interrupt\w*|retry\w*|retried|retries|denied|refused|cancel\w*|timed?\s*out|timeout|nonzero|unsuccessful|exception|traceback|segmentation fault|terminated unexpectedly|killed|sigint|sigterm|sigkill)\b')
RISK = re.compile(r'(?i)\b(?:approv\w*|permission\w*|consent|assent|authori[sz]\w*|confirm\w*|clarif\w*|decid\w*|decision\w*|choos\w*|choice\w*|objection\w*|proceed|continue|cleanup|backup\w*|delet\w*|remov\w*|truncat\w*|discard\w*|overwrit\w*|wip\w*|eras\w*|forc\w*|push\w*|releas\w*|deploy\w*|publish\w*|prod(?:uction)?|rebase|squash|money|funds|transfer\w*|invoice\w*|charg\w*|payment\w*|pay|paid|spend\w*|cost\w*|credential\w*|password\w*|secret\w*|token\w*|keychain|key|customer\w*|terraform|pending|await\w*|waiting|blocked|incomplete|remaining|except|assuming|probably|unsure|uncertain|nod|permit\w*|migration|recurring|rollback|rm|fortfahren|supprimer|sauvegardes|autorizar|pago|option|alternative|guidance|response|input|needed|question|ask)\b|\b(?:tell me|reply|respond|what do|would it|is it|are you|do i|may the|go ahead|as far as|did not|not succeed|still checking|additional feature|stored access|let me know|want me|would you|should i|shall i|can i|may i|could i|should we|shall we|can we|may we|could we)\b')

def prose_veto(text):
    # Unknown alphabets/control characters are withheld, never interpreted as assent.
    scan = normalized(text)
    return (not text.isascii() or any(ord(c) < 32 and c not in '\n\r\t' for c in text)
            or bool(re.search(r'(?im)^TURN-STATUS:\s*(needs-user|blocked)\s*$', scan))
            or bool(re.search(r'[?]|[$€£]\s*\d|\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})', scan) or BAD.search(scan) or RISK.search(scan)))

def failure_signal(value):
    if isinstance(value, dict):
        for key, item in value.items():
            key = re.sub(r'[^a-z]', '', key.lower())
            if any(part in key for part in ('error', 'fail', 'abort', 'interrupt', 'retry', 'retries', 'denied', 'cancel', 'approval', 'question', 'pendingtool', 'permissionrequest')) and item not in (False, None, '', 0, [], {}):
                return True
            if key in ('exitcode', 'exitstatus', 'returncode', 'returnstatus') and (type(item) is not int or item != 0):
                return True
            if key in ('status', 'outcome') and item not in ('success', 'completed', 'ok', 'in_progress'):
                return True
            if key in ('success', 'successful') and item is not True:
                return True
            if failure_signal(item):
                return True
    elif isinstance(value, list):
        return any(failure_signal(item) for item in value)
    elif isinstance(value, str):
        text = normalized(value)
        if BAD.search(text):
            return True
        for match in re.finditer(r'(?i)\b(?:exit(?:ed)?|return(?:ed)?)(?: with)?(?: code| status)?\s*[:=]?\s*([+-]?\d+)\b', text):
            if match[1].lstrip('+-0'):
                return True
    return False

def unfinished(value):
    if isinstance(value, dict):
        return any((key == 'status' and item == 'in_progress') or
                   ('pending' in key.lower() and bool(item)) or unfinished(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(unfinished(item) for item in value)
    return False

def timestamp(value):
    try:
        at = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return at if at.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None

def clean_completion(session):
    evidence = session.evidence
    return bool(evidence and evidence.safe and session.completion_confirmed
                and evidence.source_id in ('', session.id)
                and session.state == 'idle' and not session.parse_limited
                and not session.turn_failed and session.last_finished_at == evidence.completed
                and session.final_message == evidence.final and not prose_veto(session.final_message))

class TurnEvidence:
    """A certificate for one observed turn, independent of display/quiet fallback."""
    def __init__(self, agent, now):
        self.agent, self.now = agent, now
        self.unsupported = False
        self.bad_timestamps = False
        self.last_at = None
        self.source_id = ''
        self.reset()
    def reset(self):
        self.started = False
        self.turn_id = None
        self.user_at = self.completed = None
        self.final = ''
        self.pending = set()
        self.calls = set()
        self.failed = self.veto = self.conflict = False
        self.after_completion = False
        self.safe = False
        self.last_action = ''
        self.user_text = self.user_source = ''
    def observe(self, row, action, text='', source='', call_id='', outcome=None, summary=None):
        at = timestamp(row.get('timestamp'))
        # Only adjacent alternate user representations are aliases. A later user
        # message always starts a new turn, including an identical resumed task.
        alias = (action == 'user' and self.last_action == 'user' and text == self.user_text
                 and source != self.user_source and at == self.user_at)
        if action == 'start':
            if self.completed or self.last_action == 'abort' or self.user_at is None and self.last_action != 'start':
                self.reset()
            elif self.started:
                self.conflict = True
            self.started = True
        elif action == 'user' and not alias:
            if self.last_action != 'start' and not (self.started and self.user_at is None):
                self.reset()
            self.user_at, self.user_text, self.user_source = at, text, source
            if self.agent == 'claude':
                self.started = True
        payload = row.get('payload') if isinstance(row.get('payload'), dict) else row
        if self.agent == 'codex':
            current_id = payload.get('turn_id')
            if 'turn_id' in payload and (not isinstance(current_id, str) or not current_id):
                self.unsupported = True
            elif current_id is not None:
                if self.turn_id is not None and current_id != self.turn_id:
                    self.conflict = True
                self.turn_id = current_id
            if action == 'complete' and self.turn_id != current_id:
                self.conflict = True
        identity = payload.get('id') if row.get('type') == 'session_meta' else row.get('sessionId')
        if identity is not None:
            if not isinstance(identity, str) or not identity or self.source_id and identity != self.source_id:
                self.unsupported = True
            else:
                self.source_id = identity
        if action == 'complete' and any(v.get('status', 'completed') not in ('success', 'completed', 'ok') for v in (row, payload)):
            self.conflict = True
        if at is None or at > self.now or self.last_at and at < self.last_at:
            self.unsupported = True
            self.bad_timestamps = True
        if at:
            self.last_at = at
        if self.completed:
            self.after_completion = True
            self.safe = False
        self.failed |= failure_signal(row)
        if action == 'unknown':
            self.unsupported = True
        elif action == 'abort':
            self.failed = True
        elif action in ('agent', 'call', 'result', 'complete'):
            self.veto |= prose_veto(text)
            if not self.started or self.user_at is None:
                self.conflict = True
            if action == 'agent':
                self.final = text
            elif action == 'call':
                self.final = ''
                if not isinstance(call_id, str) or not call_id or call_id in self.calls:
                    self.conflict = True
                else:
                    self.pending.add(call_id)
                    self.calls.add(call_id)
            elif action == 'result':
                self.final = ''
                self.conflict |= unfinished(outcome)
                if not isinstance(call_id, str) or call_id not in self.pending:
                    self.conflict = True
                else:
                    self.pending.remove(call_id)
                if not isinstance(outcome, dict) or not any(
                    (k in outcome and type(outcome[k]) is int and outcome[k] == 0) for k in ('exit_code', 'exitCode', 'returncode')
                ) and not (isinstance(outcome, dict) and (outcome.get('is_error') is False or outcome.get('status') in ('success', 'completed', 'ok'))):
                    self.conflict = True
            else:
                if summary is not None and summary != self.final:
                    self.conflict = True
                self.completed = at
                self.safe = bool(self.started and self.user_at and at and at > self.user_at
                                 and self.final and not self.pending and not self.failed
                                 and not self.veto and not self.conflict and not self.unsupported
                                 and not self.after_completion)
        self.last_action = action
    @property
    def reason(self):
        if self.bad_timestamps:
            return 'invalid-timestamps'
        if self.failed:
            return 'worker-failed-or-interrupted'
        if self.unsupported:
            return 'unsupported-turn-data'
        if self.veto:
            return 'worker-needs-human'
        if self.conflict or self.after_completion:
            return 'inconsistent-turn'
        if self.completed and not self.safe:
            return 'completion-not-established'
        return ''
