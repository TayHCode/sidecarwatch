from . import LABEL

TEMPLATES = {
    'back_to_goal': "Re-read the user's stated goal and stay within that scope.",
    'already_decided': 'The user already decided this: "{quote}". Follow it. If it does not answer your question, stop and wait for the user.',
    'show_evidence': "Summarize the evidence for the work completed so far.",
    'keep_it_simple': "Keep the solution simple and within the stated scope. Do not add unrequested work.",
    'run_tests': "Run the tests for the current task and report the results.",
    'summarize': "Summarize what you have done and what remains within the stated task.",
    'housekeeping': "Check your project's housekeeping rules (working folder, scratch files, open review threads) and fix anything out of line. Do not start new work.",
    'continue': "Continue only the current task within the user's stated instructions. Stop and wait for the user if a decision is needed.",
}
CONTINUE = TEMPLATES['continue']

CLOSING = "[If this would mean a decision, a scope change, spending, credentials or a release: stop and wait for the user.]"

def valid_quote(value):
    from .policy import NEVER, UNSAFE_FINAL, hits
    return (isinstance(value,str) and 1 <= len(value) <= 200 and value.isascii()
            and all(32 <= ord(c) <= 126 for c in value) and '"' not in value
            and LABEL not in value and '{' not in value and '}' not in value
            and not any(hits(value,patterns) for patterns in NEVER.values())
            and not hits(value,[UNSAFE_FINAL]))

def body(ident, profile=None, index=-1):
    if ident != 'already_decided':
        return TEMPLATES.get(ident,'')
    values = (profile or {}).get('decided',[])
    if type(index) is not int or not 0 <= index < len(values) or not valid_quote(values[index]):
        return ''
    return TEMPLATES[ident].format(quote=values[index])

def valid_body(value):
    if value in [v for k,v in TEMPLATES.items() if k != 'already_decided']:
        return True
    before,after = TEMPLATES['already_decided'].split('{quote}')
    return value.startswith(before) and value.endswith(after) and valid_quote(value[len(before):-len(after)])
