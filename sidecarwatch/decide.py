"""The original Kind flow: current human questions, draft, and clearing on answer."""
from .scrub import scrub

def context(result, session, cfg=None):
    """Worker evidence can require the owner even if the reviewer misses it."""
    from .policy import hits, WORKER_ASKS
    if session.final_message and ('?' in session.final_message or hits(session.final_message, WORKER_ASKS)):
        result['kind'], result.reason = '2', 'worker-needs-human'
        result['question_for_user'] = scrub(session.final_message, cfg)
    return result

def needs_user(result):
    return result['kind'] == '2' or (result['should_correct'] != 'no' and result['kind'] != '1')

def record(result, session, state, profile, cfg, *, action, reason, body, needs_user):
    if needs_user:
        paused = reason in ('max-turns','repeat-reply','no-progress','cooldown','rate-limit',
                           'folder-limit','run-budget','day-limit','send-log-unarmed')
        note = 'Automatic replies paused for this session: ' if paused else ''
        text = f'Needs you: {session.agent} {session.id[:8]}\n{note}{reason}'
        if result and cfg['log']['text'] != 'none':
            text += '\n' + session.cwd
            if result['question_for_user']:
                text += '\nQuestion: ' + result['question_for_user']
            if result['draft']:
                text += '\nDraft: NOT SENT\n' + result['draft']
            elif body:
                text += '\nSuggested message: NOT SENT\n' + body
        entry = scrub(text,cfg['scrub'])
        state.need(entry,session,result,profile,cfg)
        return entry
    if action == 'sent' or result and result['kind'] == 'none':
        state.clear_need(session)
    return ''
