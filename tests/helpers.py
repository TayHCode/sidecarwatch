import datetime as dt
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
import tests

ROOT = Path(__file__).resolve().parents[1]


def text_parts(*parts):
    """Build sensitive-looking synthetic examples only during test execution."""
    return ''.join(parts)


class Sandbox:
    def __init__(self, *, consent=True):
        # Historical review regressions use an explicitly consenting synthetic
        # user. Privacy-default tests pass consent=False and use untouched config.
        self.consent = consent
    def __enter__(self):
        (ROOT / '.scratch').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.scratch')
        self.root = Path(self.tmp.name)
        self.claude = self.root / 'claude'
        self.codex = self.root / 'codex'
        self.project = self.root / 'bookshelf-app'
        self.project.mkdir()
        self.fake = self.root / 'fake'
        self.fake.mkdir()
        shutil.copytree(ROOT / 'tests/fixtures/bin', self.root / 'bin')
        # Fake CLI shebangs must use the suite's Python, including on macOS.
        (self.root / 'bin/python3').symlink_to(sys.executable)
        env = {k: v for k, v in os.environ.items() if not k.startswith('SIDECARWATCH_') and k not in ('CLAUDE_CONFIG_DIR', 'CODEX_HOME', text_parts('ANTHROPIC_API_KE', 'Y'), text_parts('ANTHROPIC_AU', 'TH_TO', 'KEN'), text_parts('OPENAI_API_KE', 'Y'), text_parts('CODEX_API_KE', 'Y'))}
        env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / 'config'), XDG_STATE_HOME=str(self.root / 'state'), SIDECARWATCH_CLAUDE_HOME=str(self.claude), SIDECARWATCH_CODEX_HOME=str(self.codex), PATH=str(self.root / 'bin') + ':/usr/bin:/bin', FAKE_CLI_DIR=str(self.fake), TMPDIR=str(ROOT / '.scratch'), GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        return self

    def __exit__(self, *args):
        self.env.stop()
        self.tmp.cleanup()

    def arm(self, clock=None):
        result = self.cli(['rearm'], clock=clock)
        assert result[0] == 0, result

    def cli(self, args, **kwargs):
        from sidecarwatch.cli import main
        from sidecarwatch.config import remember_consent, load, ConfigError
        if self.consent and args[0] in ('audit', 'watch'):
            cfg = Path(args[args.index('--config')+1]) if '--config' in args else self.root/'config/sidecarwatch/config.toml'
            try:
                load(cfg)
            except ConfigError:
                pass
            else:
                remember_consent(cfg)
        out, err = io.StringIO(), io.StringIO()
        code = main(args, stdout=out, stderr=err, **kwargs)
        return code, out.getvalue(), err.getvalue()


class FakeClock:
    def __init__(self):
        self.value = dt.datetime.now(dt.timezone.utc)
        self.on_sleep = None
        self.elapsed = time.monotonic()

    def now(self):
        return self.value

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        self.value += dt.timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep()


class FakeReviewer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def review(self, system_text, data_block):
        self.calls.append((system_text, data_block))
        item = self.verdicts.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item


class FakeDelivery:
    def __init__(self, respond=None):
        self.calls = []
        self.respond = respond

    def available(self, session):
        return True

    def send(self, session, text):
        self.calls.append((session, text))
        if self.respond:
            self.respond(session, text)
        return True


def verdict(**changes):
    from sidecarwatch.rubric import CHECK_NAMES
    value = dict(summary='Adding CSV export.',label='WORKING',checks={k:{'rating':'ok','note':''} for k in CHECK_NAMES},
        should_correct='no',correction_reason='',kind='none',template='none',decided_index=-1,
        needs_user_topics=[],worker_pushed_back=False,draft='Summarize the export work.',question_for_user='',confidence='high')
    # Translate old fixture builders once; raw attack objects keep their invalid fields.
    if 'status' in changes:
        old=changes.pop('status'); changes['label']={'working':'WORKING','done':'DONE','waiting_on_user':'NEEDS_USER','blocked':'BLOCKED','unclear':'UNSURE'}.get(old,old)
    if 'reply_kind' in changes:
        old=changes.pop('reply_kind'); changes['kind']={'nudge':'1','needs_user':'2','none':'none'}.get(old,old)
    if 'nudge_type' in changes: changes['template']=changes.pop('nudge_type')
    for old,new in [('on_task','on_goal'),('in_scope','in_scope'),('simple','kiss')]:
        if old in changes: value['checks'][new]=changes.pop(old)
    if 'reply' in changes and changes['reply'] == '': changes.pop('reply')
    value.update(changes)
    return value


def nudge(**changes):
    values={'kind':'1','template':'back_to_goal'}
    values.update(changes)
    return verdict(**values)


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r)+'\n' if isinstance(r, dict) else r+'\n' for r in rows))
    return path


def ordered(rows, at):
    for index, row in enumerate(rows):
        row['timestamp'] = (at - dt.timedelta(milliseconds=len(rows)-index)).isoformat()
    return rows


def claude_session(home, cwd, turns, **opts):
    sid = opts.get('id', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1')
    path = home/'projects'/'bookshelf-app'/(sid+'.jsonl')
    rows = []
    stamp = opts.get('at', dt.datetime.now(dt.timezone.utc)).isoformat()
    for user, agent in turns:
        rows += [{'type':'user', 'cwd':str(cwd), 'timestamp':stamp, 'message':{'content':user}}, {'type':'assistant', 'timestamp':stamp, 'message':{'content':[{'type':'text', 'text':agent}], 'stop_reason': opts.get('stop_reason', 'end_turn')}}]
    if opts.get('running'):
        rows[-1]['message']['content'].append({'type':'tool_use', 'name':'Bash', 'input':{'command':'check export'}})
        rows[-1]['message']['stop_reason']='tool_use'
    return write_lines(path, ordered(rows, opts.get('at', dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1))))


def codex_session(home, cwd, turns, shape='A', **opts):
    sid = opts.get('id', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2')
    path = home/'sessions'/'2026'/'01'/'05'/('rollout-2026-01-05T10-00-00-'+sid+'.jsonl')
    stamp = opts.get('at', dt.datetime.now(dt.timezone.utc)).isoformat()
    rows = [{'type':'session_meta', 'timestamp':stamp, 'payload':{'id':sid,'cwd':str(cwd),'source':{'subagent':'child'} if opts.get('subagent') else 'cli'}}]
    for user, agent in turns:
        event = lambda p: {'type':'event_msg','timestamp':stamp,'payload':p}
        rows.append(event({'type':'task_started'}))
        if shape == 'A':
            rows += [event({'type':'item_completed','item':{'type':kind,'content':[{'type':'text','text':text}]}}) for kind,text in [('UserMessage',user),('AgentMessage',agent)]]
        else:
            rows += [{'type':'response_item','timestamp':stamp,'payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'<environment_context>synthetic</environment_context>'}]}}, event({'type':'user_message','message':user}), {'type':'response_item','timestamp':stamp,'payload':{'type':'function_call','call_id':'c1','name':'shell','arguments':json.dumps({'command':['bash','-lc','check export']})}}, {'type':'response_item','timestamp':stamp,'payload':{'type':'function_call_output','call_id':'c1','exit_code':0,'output':'TOOL_OUTPUT_ONLY'}}, event({'type':'agent_message','message':agent}), {'type':'response_item','timestamp':stamp,'payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':agent}]}}]
        if not opts.get('running'):
            rows.append(event({'type':'task_complete','last_agent_message':agent}))
    return write_lines(path, ordered(rows, opts.get('at', dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1))))


def append_turn(path, user, agent, at=None):
    at = (at or dt.datetime.now(dt.timezone.utc)).isoformat()
    if path.name.startswith('rollout-'):
        rows = [{'type':'event_msg','timestamp':at,'payload':p} for p in [{'type':'task_started'}, {'type':'user_message','message':user}, {'type':'agent_message','message':agent}, {'type':'task_complete','last_agent_message':agent}]]
    else:
        rows = [{'type':'user','timestamp':at,'message':{'content':user}}, {'type':'assistant','timestamp':at,'message':{'content':agent,'stop_reason':'end_turn'}}]
    end = dt.datetime.fromisoformat(at)
    prior = json.loads(path.read_text().splitlines()[-1]).get('timestamp')
    begin = dt.datetime.fromisoformat(prior) if prior else end-dt.timedelta(seconds=1)
    if begin < end:
        step = (end-begin)/(len(rows)+1)
        for index,row in enumerate(rows):
            row['timestamp'] = (begin+step*(index+1)).isoformat()
    else:
        rows = ordered(rows, end)
    with path.open('a') as f:
        f.write(''.join(json.dumps(row)+'\n' for row in rows))
