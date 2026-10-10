"""Third-audit synthetic cases, preserved as test data."""
import json

FINALS = [('sounds-complete', 'That wraps up the current stage.'), ('ready', 'Ready for the next step.'), ('success-error', 'Done. One error remains.'), ('success-negative', 'All tests passed. Process exited with code -9.'), ('success-fatal', 'Done. fatal: database unavailable'), ('success-assertion', 'Done. AssertionError: result mismatch'), ('success-blockquote', 'Done.\n> The cleanup still needs approval.'), ('quoted', '"Done."'), ('single-quoted', "'Done.'"), ('blockquote', '> Done.'), ('code', '```\nDone.\n```'), ('inline-code', '`Done.`'), ('json', '{"result":"Done."}'), ('xml', '<result>Done.</result>'), ('question-statement', 'You want me to continue.'), ('question-request', 'I take it the next step is approved.'), ('question-fullwidth', 'Done？'), ('question-arabic', 'Done؟'), ('french', 'Terminé.'), ('spanish', 'Trabajo terminado.'), ('german', 'Alles erledigt.'), ('japanese', '完了しました。'), ('arabic', 'اكتمل العمل.'), ('russian', 'Работа завершена.'), ('hedge-comma', 'Done, I think.'), ('hedge-paren', 'Done. (Probably.)'), ('hedge-pending', 'Done, pending confirmation.'), ('hedge-newline', 'All tests passed.\nAs far as I can tell.'), ('unicode-width', 'ＤＯＮＥ.'), ('unicode-zero', 'Do\u200bne.'), ('ansi', '\x1b[32mDone.\x1b[0m'), ('nul', 'Done.\x00'), ('done-permission', 'Done. Proceed with deletion if you agree.'), ('done-tool-quote', 'Tool output: DONE'), ('done-user-quote', 'The user wrote: DONE'), ('done-heading', '# DONE'), ('done-comment', '<!-- DONE -->'), ('done-footer', 'Done.\n[permission required]'), ('done-backspace', 'Done.\x08?'), ('done-checklist', '- [x] Done.')]
STRUCTURES = ['tool-only-done', 'user-only-done', 'tool-quoted-done', 'user-quoted-done', 'error-negative-exit', 'error-assertion', 'error-fatal', 'error-tests-failing', 'error-permission-denied', 'error-return-code', 'error-in-item', 'contradictory-complete-summary', 'agent-after-complete', 'tool-after-complete', 'start-after-complete', 'duplicate-start', 'agent-error-field', 'wrong-channel', 'unknown-error-event', 'success-before-late-error']

def modify(rows, case, agent):
    if not case:
        return rows
    codex = agent == 'codex'

    def event(p):
        return {'type': 'event_msg', 'payload': p}

    def output(text):
        return {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': text}} if codex else {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': text}]}}
    pos = next((i for i, r in enumerate(rows) if r.get('payload', {}).get('type') in ('agent_message', 'item_completed') and (r.get('payload', {}).get('type') != 'item_completed' or r['payload']['item'].get('type') == 'AgentMessage') or r.get('type') == 'assistant'))
    errors = {'error-negative-exit': 'Process exited with code -9.', 'error-assertion': 'AssertionError: result mismatch', 'error-fatal': 'fatal: database unavailable', 'error-tests-failing': '2 tests failing.', 'error-permission-denied': 'Permission denied.', 'error-return-code': 'Command returned code 127.'}
    if case in errors:
        rows.insert(pos, output(errors[case]))
    elif case in ('tool-only-done', 'user-only-done', 'tool-quoted-done', 'user-quoted-done'):
        if codex:
            rows = [r for r in rows if not (r.get('payload', {}).get('type') == 'agent_message' or (r.get('payload', {}).get('type') == 'item_completed' and r['payload']['item'].get('type') == 'AgentMessage') or (r.get('type') == 'response_item' and r.get('payload', {}).get('role') == 'assistant'))]
            rows[-1]['payload']['last_agent_message'] = ''
            insert = len(rows) - 1
        else:
            rows = [r for r in rows if r.get('type') != 'assistant']
            insert = len(rows)
        text = 'Done.' if 'only' in case else '> Done.'
        row = output(text) if case.startswith('tool') else event({'type': 'user_message', 'message': text}) if codex else {'type': 'user', 'message': {'content': text}}
        rows.insert(insert, row)
    elif case == 'error-in-item':
        rows.insert(pos, event({'type': 'item_completed', 'item': {'type': 'LocalShellCall', 'status': 'failed', 'exit_code': 1}}) if codex else {'type': 'system', 'subtype': 'error', 'error': True})
    elif case == 'contradictory-complete-summary':
        if codex:
            for r in rows:
                p = r.get('payload', {})
                if p.get('type') == 'agent_message':
                    p['message'] = 'Your approval is required before deleting the backups.'
                if p.get('type') == 'item_completed' and p['item'].get('type') == 'AgentMessage':
                    p['item']['content'] = [{'type': 'text', 'text': 'Your approval is required before deleting the backups.'}]
                if r.get('type') == 'response_item' and p.get('role') == 'assistant':
                    p['content'] = [{'type': 'output_text', 'text': 'Your approval is required before deleting the backups.'}]
        else:
            rows[-1]['message']['content'].append({'type': 'text', 'text': 'Your approval is required before deleting the backups.'})
    elif case in ('agent-after-complete', 'tool-after-complete', 'start-after-complete', 'success-before-late-error'):
        if case == 'agent-after-complete':
            row = event({'type': 'agent_message', 'message': 'Your approval is required before deleting the backups.'}) if codex else {'type': 'assistant', 'message': {'content': 'Your approval is required before deleting the backups.'}}
        elif case == 'tool-after-complete':
            row = {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'shell', 'arguments': '{"command":"waiting for approval"}'}} if codex else {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'waiting for approval'}}], 'stop_reason': 'tool_use'}}
        elif case == 'start-after-complete':
            row = event({'type': 'task_started'}) if codex else {'type': 'user', 'message': {'content': 'Start a new step.'}}
        else:
            row = output('Command returned code 127.')
        rows.append(row)
    elif case == 'duplicate-start':
        rows.insert(pos, event({'type': 'task_started'}) if codex else {'type': 'system', 'subtype': 'turn_started'})
    elif case == 'agent-error-field':
        if codex:
            p = rows[pos]['payload']
            p['error'] = True
            p['status'] = 'failed'
        else:
            rows[pos]['error'] = True
    elif case == 'wrong-channel':
        if codex:
            rows = [r for r in rows if r.get('payload', {}).get('type') not in ('agent_message', 'item_completed') or r.get('payload', {}).get('item', {}).get('type') == 'UserMessage']
            rows = [r for r in rows if not (r.get('type') == 'response_item' and r.get('payload', {}).get('role') == 'assistant')]
            rows.insert(-1, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'tool', 'content': [{'type': 'output_text', 'text': 'Done.'}]}})
            rows[-1]['payload']['last_agent_message'] = 'Done.'
        else:
            rows[-1]['type'] = 'tool'
    elif case == 'unknown-error-event':
        rows.insert(pos, {'type': 'error', 'error': 'Agent crashed', 'exit_code': 1})
    return rows
