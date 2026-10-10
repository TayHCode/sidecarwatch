"""Persist worker problems even when the worker is not eligible for review."""
from .turnstate import clean_completion
from .scrub import scrub, ScrubUncertain

def record(session, reason, state, cfg, now):
    from .report import explain
    entry = scrub(f'Needs you: {session.agent} {session.id[:8]}\nAutomatic replies paused for this session: {explain(reason)}\nAutomatic reply: NOT SENT', cfg['scrub'])
    question = (scrub(session.final_message, cfg['scrub'])
                if reason == 'worker-needs-human' and cfg['log']['text'] != 'none' else '')
    if question:
        entry += '\nQuestion: ' + question
    stamp = session.evidence_key + ':' + reason
    row = state.session(session)
    if row.get('observation') == stamp and session.agent+':'+session.id in state.data['pauses']:
        return None
    row['observation'] = stamp
    state.need(entry, session, {'question_for_user': question}, cfg=cfg)
    result = dict(time=now.isoformat(), agent=session.agent, session=session.id[:8],
                  turn_key=(session.turn_key), action='refused' if reason.startswith('scrub-uncertain:') else 'flagged', reason=reason)
    state.log(result, cfg['log'])
    return result

def reason(session, state, deadline=False):
    refusal = state.session(session).get('privacy_refusal', {})
    if refusal.get('evidence_key') == session.evidence_key and refusal.get('kind'):
        return str(ScrubUncertain(refusal['kind']))
    if session.parse_limited:
        return 'incomplete-transcript: skipped malformed or oversized JSON/string'
    e = session.evidence
    if e and e.bad_timestamps:
        return 'invalid-timestamps'
    if e and e.failed:
        return e.reason
    if (session.state != 'idle' or state.handled(session)) and e and e.reason:
        return e.reason
    if deadline and not state.data['pauses'].get(session.agent+':'+session.id):
        if not clean_completion(session):
            return 'worker-unresolved-at-deadline'
        if state.session(session).get('last_sent_turn') == session.turn_key:
            return 'awaiting-worker-at-deadline'
    return ''
