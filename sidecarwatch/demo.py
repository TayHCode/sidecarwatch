import json
from pathlib import Path
import tempfile
from . import digest, profiles, report, policy
from .claude_format import parse as claude_parse
from .codex_format import parse as codex_parse
from .reviewer import validate
from .state import State

def run(cfg, out, clock):
    print('DEMO: synthetic sessions; no CLI calls.', file=out)
    with tempfile.TemporaryDirectory() as folder:
        root, now = Path(folder), clock.now()
        common = {'timestamp': now.isoformat()}
        rows = [dict(common, type='session_meta', payload={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2','cwd':'/work/bookshelf-app','source':'cli'})]
        for payload in [{'type':'task_started'}, {'type':'user_message','message':'Add CSV export to the reading list. Use the standard csv module.'}, {'type':'agent_message','message':'The export is added. I am checking a small export test.'}, {'type':'task_complete'}]:
            rows.append(dict(common, type='event_msg', payload=payload))
        path = root / 'rollout-sample.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        session = codex_parse(path, now, 60)
        other = root / 'sample.jsonl'
        other.write_text(json.dumps(dict(common, type='user', cwd='/work/bookshelf-app', message={'content':'Check CSV export.'})) + '\n' + json.dumps(dict(common, type='assistant', message={'content':'The export test covers two rows.', 'stop_reason':'end_turn'})) + '\n')
        claude_parse(other, now, 60)
        block = digest.build(session, profiles.DEFAULT, cfg)
        from .rubric import CHECK_NAMES
        result = validate(dict(summary='Adding CSV export to the reading list and checking a small export test.',
            label='WORKING',checks={k:{'rating':'ok','note':''} for k in CHECK_NAMES},should_correct='no',
            correction_reason='',kind='none',template='none',decided_index=-1,needs_user_topics=[],
            worker_pushed_back=False,draft='Report the export test results when they are available.',
            question_for_user='',confidence='high'),cfg['scrub'])
        policy.decide(session, result, State(root/'state'), cfg, policy.Options(), now)
        print(report.audit(session, result, cfg['scrub'], 'claude'), file=out)
    print('DEMO complete: temporary sessions removed; nothing sent.', file=out)
    return 0
