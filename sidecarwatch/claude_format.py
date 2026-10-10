import datetime as dt
from pathlib import Path
from .sessions import Session, obj, string, content, stamp, lines

from .turnstate import failure_signal, TurnEvidence
from .event_shapes import claude as observe_turn

SKIP = ('<command-name>', '<command-message>', '<local-command-', '<system-reminder>', '<task-notification>', 'Caveat:', '[Request interrupted')

def parse(path, now, quiet_seconds):
    path = Path(path)
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    s = Session('claude', path.stem, path, updated_at=modified)
    s.evidence = TurnEvidence('claude', now)
    quiet = (now - modified).total_seconds() >= quiet_seconds
    pending, assistant, tools, result_after, ended = False, None, False, False, False
    def close():
        if pending and assistant and not tools and not result_after and (ended or quiet):
            s.finish(*assistant, confirmed=ended)
    for row in lines(path, s):
        observe_turn(s.evidence, row)
        if row.get('isSidechain') is True:
            continue
        s.cwd = string(row.get('cwd')) or s.cwd
        at = stamp(row.get('timestamp'), modified)
        s.updated_at = at
        kind, message = row.get('type'), obj(row.get('message'))
        body = message.get('content')
        blocks = [obj(x) for x in body] if isinstance(body, list) else []
        if kind == 'user':
            if any(b.get('type') == 'tool_result' for b in blocks):
                s.turn_failed |= failure_signal(blocks)
                result_after = True
                continue
            text = content(body)
            if row.get('isMeta') or row.get('isCompactSummary') or not text or text.startswith(SKIP):
                continue
            close()
            s.user(at, text)
            pending, assistant, tools, result_after, ended = True, None, False, False, False
        elif kind == 'assistant':
            s.turn_failed |= failure_signal(row)
            text = body if isinstance(body, str) else '\n'.join(string(b.get('text')) for b in blocks if b.get('type') == 'text')
            s.event(at, 'agent', text)
            assistant, tools, result_after = (at, text), False, False
            ended = message.get('stop_reason') in ('end_turn', 'stop_sequence')
            for block in blocks:
                if block.get('type') == 'tool_use':
                    tools = True
                    data = obj(block.get('input'))
                    detail = next((string(data.get(k)) for k in ('command', 'file_path', 'path', 'pattern', 'description') if isinstance(data.get(k), str)), '')
                    s.event(at, 'tool', string(block.get('name')) + ': ' + detail)
        elif kind == 'system':
            s.turn_failed |= failure_signal(row)
            ended = ended or row.get('subtype') == 'turn_duration'
        elif kind not in ('summary', 'progress', 'file-history-snapshot'):
            s.unknown_lines += 1
    close()
    if not s.messages:
        s.state = 'unknown'
    s.completion_confirmed = s.evidence.safe and not s.parse_limited
    s.turn_failed = s.evidence.failed
    return s
