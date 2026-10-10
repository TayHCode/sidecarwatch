"""Small, explicit transcript grammar. Unknown structures never certify a turn."""
import json
from .turnstate import prose_veto
from .scrub import bounded_json

FLAGS = set('error is_error status outcome exit_code exitCode returncode success successful retry_count requires_approval pending_approval pending_question pending_tool_calls interrupted aborted failed'.split())

def fields(e, value, allowed, *, envelope=False):
    if not envelope and set(value) - set(allowed.split()) - FLAGS:
        e.unsupported = True
    for key in ('is_error', 'interrupted', 'aborted', 'failed', 'requires_approval', 'pending_approval', 'pending_question'):
        if key in value and type(value[key]) is not bool:
            e.unsupported = True
    if 'retry_count' in value and type(value['retry_count']) is not int:
        e.unsupported = True

def text_blocks(value, kinds):
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or any(not isinstance(b, dict) or set(b) - {'type', 'text', 'text_elements'} or b.get('type') not in kinds or not isinstance(b.get('text'), str) or b.get('text_elements', []) != [] for b in value):
        return None
    return '\n'.join(b['text'] for b in value)

def codex(e, row):
    p = row.get('payload')
    if not isinstance(p, dict):
        e.observe(row, 'unknown'); return
    fields(e, row, 'type timestamp payload', envelope=True)
    kind, typ = row.get('type'), p.get('type')
    allowed = {
        'session_meta': 'id cwd source originator', 'turn_context': 'cwd',
        'task_started': 'type turn_id', 'turn_started': 'type turn_id',
        'task_complete': 'type turn_id last_agent_message', 'turn_complete': 'type turn_id last_agent_message',
        'user_message': 'type message', 'agent_message': 'type message', 'item_completed': 'type item',
        'message': 'type role content id phase end_turn', 'function_call': 'type call_id name arguments',
        'custom_tool_call': 'type call_id name input', 'local_shell_call': 'type call_id name action',
        'function_call_output': 'type call_id output results children',
        'custom_tool_call_output': 'type call_id output results children',
        'local_shell_call_output': 'type call_id output results children',
        'agent_reasoning': 'type text', 'token_count': 'type info rate_limits',
        'reasoning': 'type text summary content encrypted_content id',
    }
    extra = {'task_started': ' collaboration_mode_kind model_context_window', 'user_message': ' images local_images text_elements',
             'agent_message': ' phase', 'task_complete': ' completed_at_ms'}
    fields(e, p, allowed.get(kind if kind in ('session_meta','turn_context') else str(typ), 'type') + ' turn_id' + extra.get(str(typ), ''),
           envelope=kind in ('session_meta', 'turn_context'))
    action, text, source, cid, outcome, summary = 'unknown', '', '', '', None, None
    if kind == 'session_meta':
        action = 'metadata' if e.user_at is None and isinstance(p.get('id'), str) and isinstance(p.get('cwd'), str) else 'unknown'
    elif kind == 'turn_context':
        action = 'metadata' if isinstance(p.get('cwd'), str) else 'unknown'
    elif kind == 'event_msg':
        if typ in ('task_started', 'turn_started'):
            action = 'start'
        elif typ in ('task_complete', 'turn_complete'):
            action, summary = 'complete', p.get('last_agent_message')
        elif typ in ('turn_aborted', 'error', 'task_failed', 'turn_failed'):
            action = 'abort'
        elif typ in ('user_message', 'agent_message'):
            action, text, source = ('user' if typ == 'user_message' else 'agent'), p.get('message'), 'event'
        elif typ == 'item_completed' and isinstance(p.get('item'), dict):
            item = p['item']
            fields(e, item, 'type content id phase delivery')
            if item.get('type') in ('UserMessage', 'AgentMessage'):
                action = 'user' if item['type'] == 'UserMessage' else 'agent'
                text, source = text_blocks(item.get('content'), ('text',)), 'item'
        elif typ == 'agent_reasoning' and isinstance(p.get('text'), str):
            action, text = 'reasoning', p['text']
            e.veto |= prose_veto(text)
        elif typ == 'token_count':
            action = 'metadata'
    elif kind == 'response_item':
        if typ == 'message' and p.get('role') in ('user', 'assistant'):
            action = 'user' if p['role'] == 'user' else 'agent'
            text, source = text_blocks(p.get('content'), ('input_text', 'output_text', 'text')), 'response'
            if action == 'user' and isinstance(text, str) and text.startswith(('<environment_context', '<user_instructions', '<permissions', '<system', '# AGENTS.md')):
                action = 'metadata' if e.user_at is None else 'unknown'
        elif typ in ('function_call', 'custom_tool_call', 'local_shell_call'):
            action, cid = 'call', p.get('call_id')
            args = p.get('arguments', p.get('input', p.get('action')))
            if isinstance(args, str):
                text = args
                if args.lstrip().startswith(('{', '[')):
                    try:
                        bounded_json(args)
                    except ValueError:
                        action = 'unknown'
            elif isinstance(args, dict):
                text = json.dumps(args)
            else:
                action = 'unknown'
        elif typ in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
            action, cid, outcome = 'result', p.get('call_id'), p
            output = p.get('output')
            text = output if isinstance(output, str) else ''
            if not isinstance(output, str):
                action = 'unknown'
            if isinstance(output, str) and output.lstrip().startswith(('{', '[')):
                try:
                    decoded = bounded_json(output)
                    # Nested metadata may veto, but cannot replace the CLI outcome.
                    from .turnstate import failure_signal, unfinished
                    e.failed |= failure_signal(decoded)
                    e.conflict |= unfinished(decoded)
                    from .markers import strings
                    e.veto |= any(prose_veto(part) for part in strings(decoded))
                except ValueError:
                    action = 'unknown'
        elif typ == 'reasoning':
            action = 'reasoning'
            text = json.dumps(p)
            e.veto |= prose_veto(text)
    if not isinstance(text, str):
        action, text = 'unknown', ''
    e.observe(row, action, text, source, cid, outcome, summary)

def claude(e, row):
    fields(e, row, 'type timestamp cwd uuid parentUuid sessionId isSidechain isMeta isCompactSummary message subtype durationMs requestId', envelope=True)
    if any(row.get(key) not in (None, False) for key in ('isSidechain', 'isMeta', 'isCompactSummary')):
        e.observe(row, 'unknown'); return
    kind, message = row.get('type'), row.get('message')
    if isinstance(message, dict):
        fields(e, message, 'role content stop_reason id model usage type container context_management diagnostics input_transformations stop_details stop_sequence')
        if message.get('role', kind) != ('assistant' if kind == 'assistant' else 'user' if kind == 'user' else kind):
            e.observe(row, 'unknown'); return
    if kind in ('user', 'assistant') and isinstance(message, dict):
        body = message.get('content')
        if kind == 'user':
            if isinstance(body, list) and body and all(isinstance(b, dict) and b.get('type') == 'tool_result' for b in body):
                for block in body:
                    fields(e, block, 'type tool_use_id content results children')
                    value = block.get('content')
                    text = text_blocks(value, ('text',))
                    if text is None:
                        e.observe(row, 'unknown')
                    else:
                        e.observe(row, 'result', text, call_id=block.get('tool_use_id'), outcome=block)
                return
            text = text_blocks(body, ('text',))
            if isinstance(text, str) and text.startswith('[Request interrupted'):
                e.observe(row, 'abort'); return
            if row.get('isMeta') or row.get('isCompactSummary') or row.get('isSidechain'):
                e.observe(row, 'unknown'); return
            if isinstance(text, str) and not text.startswith(('<', 'Caveat:')):
                e.observe(row, 'user', text, 'user'); return
        else:
            blocks = [{'type': 'text', 'text': body}] if isinstance(body, str) else body
            if not isinstance(blocks, list) or not blocks:
                e.observe(row, 'unknown'); return
            texts, calls = [], []
            for block in blocks:
                if not isinstance(block, dict):
                    e.observe(row, 'unknown'); return
                fields(e, block, 'type text thinking signature id name input caller')
                if block.get('type') == 'text' and isinstance(block.get('text'), str):
                    texts.append(block['text'])
                elif block.get('type') == 'tool_use' and isinstance(block.get('input'), dict):
                    calls.append(block)
                elif block.get('type') == 'thinking' and isinstance(block.get('thinking'), str):
                    e.veto |= prose_veto(block['thinking'])
                else:
                    e.observe(row, 'unknown'); return
            if texts:
                e.observe(row, 'agent', '\n'.join(texts))
            for block in calls:
                e.observe(row, 'call', json.dumps(block['input']), call_id=block.get('id'))
            stop = message.get('stop_reason')
            if stop == 'end_turn' and not calls:
                e.observe(row, 'complete')
            elif stop not in ('tool_use', None) or stop == 'tool_use' and not calls:
                e.observe(row, 'unknown')
            return
    if kind == 'system' and row.get('subtype') == 'turn_duration':
        e.observe(row, 'metadata')
    else:
        e.observe(row, 'unknown')
