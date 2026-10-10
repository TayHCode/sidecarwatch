import json
import re
from .scrub import scrub, display_scrub, label_only
from .profiles import match

PRIVACY_RESIDUE = ('Phone numbers and unseparated SSN-style IDs may remain raw in the digest sent after consent. '
                   'Names, addresses and other personal data may remain too; remove them at the source before review.')

def session_id(value, cfg):
    """Display recognized session identifiers without matching numeric groups as cards."""
    uuid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    if re.fullmatch(r'(?:(?:rollout-\d{4}-\d{2}-\d{2}T)?\d{2}-\d{2}-\d{2}-)?' + uuid, value):
        if not any(re.search(p, value) for p in cfg['extra_patterns']) and not any(t and t in value for t in cfg['extra_terms']):
            return value
    return scrub(value, cfg)

def safe(value, cfg):
    if isinstance(value, str):
        return scrub(value, cfg)
    if isinstance(value, dict):
        return {key: safe(item, cfg) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item, cfg) for item in value]
    return value

def metadata(s, profiles, cfg):
    value = safe(dict(agent=s.agent, name=s.name, cwd=s.cwd, folders=sorted(s.folders), state=s.state, updated_at=s.updated_at.isoformat(), turns_finished=s.turns_finished, last_finished_at=s.last_finished_at.isoformat() if s.last_finished_at else None, turn_key=(s.turn_key), profile=match(s, profiles)['name'], unknown_lines=s.unknown_lines, total_lines=s.total_lines, parse_notes=s.parse_notes), cfg)
    value['id'] = session_id(s.id, cfg)
    return value

def listing(items, profiles, cfg, now, as_json=False):
    if as_json:
        return json.dumps([metadata(s, profiles, cfg) for s in items])
    if not items:
        return 'No sessions in this scope.'
    rows = [['agent', 'id', 'state', 'age', 'folder', 'profile', 'title']]
    for s in items:
        mark = '?' if s.unknown_lines > s.total_lines / 2 else ''
        age = max(0, int((now-s.updated_at).total_seconds()))
        age_text = next(str(age//size)+unit for size, unit in ((86400, 'd'), (3600, 'h'), (60, 'm'), (1, 's')) if age >= size or size == 1)
        rows.append([s.agent, s.id, s.state+mark, age_text, s.cwd, match(s, profiles)['name'], display_scrub(s.first_request, cfg)[:60]])
    rows = [[' '.join((session_id(value, cfg) if index == 1 else scrub(value, cfg)).split())
             for index, value in enumerate(row)] for row in rows]
    widths = [max(len(row[i]) for row in rows) for i in range(6)]
    table = '\n'.join('  '.join(value.ljust(widths[i]) if i < 6 else value for i, value in enumerate(row)) for row in rows)
    return table

def conversation(s, cfg, since=None, chars=1500, tools=False, as_json=False, last=None):
    entries, pending_label = [], False
    for at, role, text in s.messages:
        clean = '[REDACTED:assignment]' if pending_label else display_scrub(text, cfg)
        pending_label = label_only(text.strip())
        if (since is None or at >= since) and (tools or role != 'tool'):
            entries.append({'time': at.isoformat(), 'role': role, 'text': clean[:min(chars, 160) if role == 'tool' else chars]})
    if last is not None:
        entries = entries[-last:]
    if as_json:
        return json.dumps(entries)
    return '\n'.join(f'{row["time"][11:16]} {row["role"]}: {row["text"]}' for row in entries)

def review_notice(who, cfg):
    vendor = 'Anthropic' if who == 'claude' else 'OpenAI'
    return (f'Review would send a scrubbed digest of finished turns, including the request, recent turn text, '
            f'tool summaries and your configured profile, to the {who} CLI ({vendor}) '
            f'using command {cfg["reviewer"][who]["command"]}. '
            'Cross-vendor review is OFF. Nothing was sent. '
            + PRIVACY_RESIDUE + ' ' +
            'Enable it explicitly with init --allow-cross-vendor-review or --allow-cross-vendor-review on audit/watch; '
            'the choice is saved only in your own config. Check it with status.')

def explain(reason):
    explanations = {
        'invalid-timestamps': 'Timestamps are missing, invalid, out of order or in the future; inspect the synthetic/session source locally.',
        'unsupported-turn-data': 'The turn contains an unsupported event or message shape; inspect doctor --shapes and docs/event-shapes.md.',
        'inconsistent-turn': 'Turn order, completion or tool outcomes disagree; inspect the worker locally.',
        'worker-failed-or-interrupted': 'A failure or interruption was observed; inspect the worker locally.',
        'worker-needs-human': 'The worker may need input or permission; inspect the worker locally.',
        'completion-not-established': 'A supported normal completion has not been established.',
    }
    return reason + (': ' + explanations[reason] if reason in explanations else '')

def audit(session, verdict, cfg, reviewer, as_json=False):
    if as_json:
        return json.dumps({'agent':session.agent,'id':session.id,'reviewer':reviewer,'verdict':dict(verdict)})
    labels = [('on_goal','On task'),('in_scope','In scope'),('kiss','Simple'),
              ('not_rebuilding','Reuse'),('root_cause','Root cause'),('honest','Honest')]
    text = (f'{session.agent} session {session.id[:8]} ({session.state})\nWhat it is doing\n{verdict["summary"]}\n\n'
            + '\n'.join(f'{label}: {verdict["checks"][key]["rating"]}' +
              (': '+verdict['checks'][key]['note'] if verdict['checks'][key]['note'] else '') for key,label in labels)
            + f'\n\nShould you correct it? {verdict["should_correct"]}\n{verdict["correction_reason"]}\nLabel: {verdict["label"]}'
            + '\nSuggested message (not sent)\n'+verdict['draft'])
    if verdict['kind'] == '2':
        text += '\nNeeds you: ' + (getattr(verdict, 'reason', '') or 'reviewer-needs-human')
        if verdict['question_for_user']:
            text += '\nQuestion: ' + verdict['question_for_user']
        if verdict['draft']:
            text += '\nDraft: NOT SENT\n' + verdict['draft']
    return scrub(text,cfg)

def daily(record):
    checks=record.get('checks',{})
    concerns=', '.join(key+': '+value['note'] for key,value in checks.items() if value['rating']=='concern') or 'all ok'
    return (f"### {record.get('name') or record['session']}\n"
            f"- **{record.get('label','UNREVIEWED')}**: {record.get('summary','')}\n"
            f"- checks: {concerns}\n- kind: {record.get('kind','none')} → {record['action']} ({record['reason']})\n"
            f"- draft: {record.get('draft','')}\n")

def paste(body):
    return '--------\n' + body + '\n--------\nNothing was sent.'

def doctor(cfg, state, homes, config_path, config_dir, items, as_json, shapes):
    from . import __version__
    from .reviewer import Capabilities, ReviewError, run
    from .deliver import Delivery
    from .sessions import Session
    from pathlib import Path
    probe = Capabilities(cfg)
    result = {'version': __version__, 'state_folder': str(state.root), 'kill_switch': state.stopped(), 'cross_vendor_review': cfg['privacy']['allow_cross_vendor_review'], 'config': str(config_path) if config_path.exists() else '(none)', 'profiles': [str(p) for p in sorted((config_dir/'profiles').glob('*.toml'))], 'clis': {}}
    flags = ('--no-session-persistence', '--json-schema', '--tools', '--system-prompt', '--ephemeral', '--output-schema', '--skip-git-repo-check')
    for agent in ('claude', 'codex'):
        try:
            code, version = run([cfg['reviewer'][agent]['command'], '--version'])
        except ReviewError:
            code, version = 1, ''
        help_text = probe.help(agent) + (probe.help(agent, 'exec') if agent == 'codex' else '')
        found = [s for s in items if s.agent == agent]
        status = probe.status(agent)
        scan = str(homes[agent] / ('projects/*/*.jsonl' if agent == 'claude' else 'sessions/**/rollout-*.jsonl'))
        row = {'found': status['ready'], 'version': version.strip() if code == 0 else '', 'home': str(homes[agent]), 'scan': scan, 'sessions': len(found), 'reviewer': status, 'reviewer_flags': {flag: flag in help_text for flag in flags}, 'delivery': Delivery(cfg, state, probe).available(Session(agent, '', Path('.'), cwd=str(Path.cwd())))}
        if shapes:
            counts = {}
            for s in found:
                for shape, count in s.shapes.items():
                    counts[shape] = counts.get(shape, 0) + count
            row.update(shapes=counts, unknown_lines=sum(s.unknown_lines for s in found), mostly_unknown=sum(s.unknown_lines > s.total_lines / 2 for s in found))
        result['clis'][agent] = row
    result = safe(result, cfg['scrub'])
    if as_json:
        return json.dumps(result, indent=2)
    lines = [f"Version: {result['version']}", f"State folder: {result['state_folder']}",
             f"Kill switch: {'on' if result['kill_switch'] else 'off'}", f"Config: {result['config']}"]
    lines.append('Profiles: ' + (', '.join(result['profiles']) or '(none)'))
    lines.append('Cross-vendor review: ' + ('ON' if result['cross_vendor_review'] else 'OFF'))
    for agent, row in result['clis'].items():
        lines += [f"{agent}: {'non-interactive review available' if row['found'] else 'review unavailable'} {row['version']}",
                  f"  Home: {row['home']}; sessions: {row['sessions']}; delivery: {'available' if row['delivery'] else 'print only'}",
                  f"  Scanned: {row['scan']}",
                  f"  Login: {row['reviewer']['login']}; {row['reviewer']['reason'] or 'capability checks passed; login unverified when status is unsupported'}",
                  '  Reviewer flags: ' + ', '.join(flag for flag, found in row['reviewer_flags'].items() if found)]
        if shapes:
            lines.append('  Shapes: ' + (', '.join(f'{key}={count}' for key, count in row['shapes'].items()) or '(none)'))
            lines.append(f"  Unknown lines: {row['unknown_lines']}; mostly unknown sessions: {row['mostly_unknown']}")
    return '\n'.join(lines)

def status_blocks(items, profiles, cfg, now, chars=500):
    from pathlib import Path
    from .sessions import branch
    if not items:
        return 'No sessions in this scope.'
    blocks = []
    for s in items:
        age = max(0, int((now-s.updated_at).total_seconds()//60))
        where = s.cwd.replace(str(Path.home()), '~')
        messages = [text for _, role, text in s.messages if role == 'agent']
        last = scrub(messages[-1], cfg)[:chars] if messages else ''
        title = scrub(s.name, cfg) if s.name else session_id(s.id, cfg)
        blocks.append(f"## {title} [{s.state.upper()}, {age}m ago]\n"
                      f"- where: {scrub(where, cfg)} ({branch(s.cwd)})\n- last message:\n" +
                      '\n'.join('  > '+line for line in last.splitlines()))
    return '\n\n'.join(blocks)
