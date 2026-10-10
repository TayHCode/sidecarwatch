import datetime as dt
import json
from pathlib import Path
import re
from .scrub import bounded_json
from .sessions import Session, obj, string, content, stamp, lines

from .turnstate import failure_signal, TurnEvidence
from .event_shapes import codex as observe_turn

SKIP = ('<environment_context', '<user_instructions', '<permissions', '<turn_aborted', '<system', '# AGENTS.md')

def parse(path, now, quiet_seconds):
    path = Path(path)
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    ids = re.findall(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', path.name)
    s = Session('codex', ids[0] if ids else path.stem, path, updated_at=modified)
    s.evidence = TurnEvidence('codex', now)
    preference, explicit = 3, False
    for row in lines(path):
        p = obj(row.get('payload'))
        if row.get('type') == 'event_msg':
            if p.get('type') == 'user_message':
                preference = 1
            elif p.get('type') == 'item_completed' and obj(p.get('item')).get('type') == 'UserMessage':
                preference = min(preference, 2)
            if p.get('type') in ('task_started', 'turn_started', 'task_complete', 'turn_complete', 'turn_aborted'):
                explicit = True
    last_agent, last_source, last_role, open_turn = '', '', '', False
    for row in lines(path, s):
        observe_turn(s.evidence, row)
        p, kind = obj(row.get('payload')), row.get('type')
        typ, at = p.get('type'), stamp(row.get('timestamp'), modified)
        s.updated_at = at
        text, role, source = '', '', ''
        if kind == 'session_meta':
            s.id = string(p.get('id')) or s.id
            s.cwd = string(p.get('cwd')) or s.cwd
            s.subagent = 'subagent' in obj(p.get('source'))
        elif kind == 'turn_context':
            s.cwd = string(p.get('cwd')) or s.cwd
        elif kind == 'event_msg':
            if typ in ('task_started', 'turn_started'):
                open_turn, s.state = True, 'running'
            elif typ in ('task_complete', 'turn_complete'):
                if open_turn:
                    s.turn_failed |= failure_signal(p)
                    s.finish(at, last_agent, confirmed=True)
                open_turn = False
            elif typ == 'turn_aborted':
                open_turn, s.state = False, 'unknown'
                s.completion_confirmed = False
                s.turn_failed = True
            elif typ == 'user_message':
                if preference == 1:
                    text, role = string(p.get('message')), 'user'
            elif typ == 'agent_message':
                text, role, source = string(p.get('message')), 'agent', 'event'
            elif typ == 'item_completed':
                item = obj(p.get('item'))
                if item.get('type') == 'UserMessage' and preference == 2:
                    text, role = content(item.get('content')), 'user'
                elif item.get('type') == 'AgentMessage':
                    text, role, source = content(item.get('content')), 'agent', 'item'
                else:
                    s.unknown_lines += 1
            elif typ in ('error', 'task_failed', 'turn_failed'):
                s.turn_failed = True
            elif typ not in ('token_count', 'agent_reasoning'):
                s.unknown_lines += 1
        elif kind == 'response_item':
            if typ == 'message':
                if p.get('role') == 'assistant':
                    text, role, source = content(p.get('content')), 'agent', 'response'
                elif p.get('role') == 'user' and preference == 3:
                    text, role = content(p.get('content')), 'user'
                    if text.startswith(SKIP):
                        text = ''
            elif typ in ('function_call', 'local_shell_call', 'custom_tool_call'):
                args = p.get('arguments', p.get('input', p.get('action', '')))
                if isinstance(args, str):
                    try:
                        args = bounded_json(args)
                    except (ValueError, RecursionError):
                        pass
                text, role = string(p.get('name')) + ': ' + (args if isinstance(args, str) else json.dumps(args)), 'tool'
            elif typ in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
                s.turn_failed |= failure_signal(p)
            elif typ != 'reasoning':
                s.unknown_lines += 1
        else:
            s.unknown_lines += 1
        if not text:
            continue
        if role == 'user':
            if not explicit and open_turn and last_role == 'agent':
                s.finish(s.messages[-1][0], last_agent)
            s.user(at, text)
            open_turn, last_agent = True, ''
        elif role == 'agent':
            s.turn_failed |= failure_signal(text)
            if text == last_agent and last_role == 'agent' and source != last_source:
                continue
            s.event(at, role, text)
            last_agent, last_source = text, source
        else:
            s.event(at, role, text)
        last_role = role
    if not explicit and open_turn and last_role == 'agent' and (now - modified).total_seconds() >= quiet_seconds:
        s.finish(s.messages[-1][0], last_agent)
    if not s.messages:
        s.state = 'unknown'
    s.completion_confirmed = s.evidence.safe and not s.parse_limited
    s.turn_failed = s.evidence.failed
    if s.evidence.after_completion and s.evidence.last_action in ('agent', 'call', 'result'):
        s.state = 'running'
    return s
