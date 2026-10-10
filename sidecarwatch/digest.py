import secrets
from pathlib import Path
from . import APP_NAME
from .profiles import text as profile_text
from .scrub import scrub, ScrubUncertain, review_check

def middle(text, cap):
    if len(text) <= cap:
        return text
    marker = '[… cut …]'
    if cap < len(marker):
        return text[:max(0, cap)]
    left = (cap-len(marker))//2
    right = cap-len(marker)-left
    return text[:left] + marker + (text[-right:] if right else '')

def build(session, profile, cfg, *, since=None, overnight=False, nonce=None):
    if session.parse_limited:
        raise ScrubUncertain('malformed-transcript')
    review_check([session.first_request, session.latest_instruction, session.final_message,
                  *[text for _, _, text in session.activity], profile_text(profile)], cfg['scrub'])
    nonce = nonce or secrets.token_hex(8)
    clean = lambda value: scrub(value.replace(nonce, ''), cfg['scrub'], strict=True)
    first = clean(session.first_request)[:2000]
    latest = clean(session.latest_instruction)[:1500] if session.latest_instruction != session.first_request else '(same as first request)'
    final = clean(session.final_message)[:4000] or '(the turn is still running)'
    cap = cfg['digest']['max_chars']
    cwd = clean(session.cwd.replace(str(Path.home()), '~'))[:max(0, min(200, cap-450))]
    profile_body = clean(profile_text(profile))
    activity = [f'- [{role}] ' + clean(text)[:160 if role == 'tool' else 300] for at, role, text in session.activity if since is None or at >= since][-cfg['digest']['recent_events']:]
    def compose():
        return (f'{APP_NAME.upper()}-REVIEW-REQUEST v1\nCONTINUE_ALLOWED: {"yes" if overnight else "no"}\n\n'
                f'<<<PROFILE {nonce}\n{profile_body}\nPROFILE {nonce}>>>\n\n'
                f'<<<DIGEST {nonce} (untrusted data copied from a transcript)\n'
                f'agent: {session.agent}\nsession_id: {clean(session.id)[:36]}\nsession_name: {clean(session.name)[:60]}\nstate: {session.state}\nworking_folder: {cwd}\nautomatic_replies_in_a_row: {session.auto_turns}\n\n'
                f'FIRST REQUEST:\n{first}\n\nLATEST INSTRUCTION:\n{latest}\n\nRECENT ACTIVITY (oldest first):\n' + '\n'.join(activity) +
                f'\n\nLAST MESSAGE OF THE FINISHED TURN:\n{final}\nDIGEST {nonce}>>>')
    while activity and len(compose()) > cap:
        activity.pop(0)
    overflow = max(0, len(compose())-cap)
    final = middle(final, max(0, len(final)-overflow))
    # Tiny configured budgets also need bounded owner text, while keeping delimiters.
    for key in ('profile', 'latest', 'first'):
        overflow = max(0, len(compose())-cap)
        if key == 'profile':
            profile_body = middle(profile_body, max(0, len(profile_body)-overflow))
        elif key == 'latest':
            latest = middle(latest, max(0, len(latest)-overflow))
        else:
            first = middle(first, max(0, len(first)-overflow))
    return compose()
