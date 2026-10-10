"""Drive the public CLI with synthetic sessions and recording fakes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 12, 12, tzinfo=timezone.utc)

@dataclass
class Outcome:
    reviewed: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    needs_you: dict = field(default_factory=dict)
    seen: dict = field(default_factory=dict)
    alerts: str = ''
    output: str = ''
    prompts: list = field(default_factory=list)
    escalated: list = field(default_factory=list)
    error: str = ''

class Sandbox:
    def __enter__(self):
        scratch = ROOT / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.synthetic_root = Path(self.temp.name)
        self.root = self.synthetic_root / 'archive'
        self.root.mkdir()
        self.codex = self.synthetic_root / '.codex'
        (self.codex / 'sessions').mkdir(parents=True)
        (self.codex / 'session_index.jsonl').write_text('')
        self.profiles = self.root / 'projects'
        self.profiles.mkdir()
        (self.profiles / '_default.md').write_text('Default synthetic profile.\n')
        self.paths = {}
        return self

    def __exit__(self, *args):
        self.temp.cleanup()

    def session(self, number=1, *, name=None, cwd=None, subagent=False, complete=True):
        sid = f'{number:08d}-1111-4111-8111-111111111111'
        folder = cwd or str(self.synthetic_root / 'code' / 'bookshelf-app')
        Path(folder).mkdir(parents=True, exist_ok=True)
        path = self.codex / 'sessions' / f'rollout-{sid}.jsonl'
        self.paths[sid] = path
        with (self.codex / 'session_index.jsonl').open('a') as handle:
            handle.write(json.dumps({'id':sid,'thread_name':name or f'Example worker {number}'})+'\n')
        path.write_text(json.dumps({'type':'session_meta','payload':{'id':sid,'cwd':folder,
            'source':{'subagent':'synthetic'} if subagent else 'cli'},'timestamp':(NOW-timedelta(seconds=10)).isoformat()})+'\n')
        self.start_turn(sid)
        self.append(sid, 'UserMessage', 'Add CSV export to the reading list.')
        self.append(sid, 'AgentMessage', 'Done.')
        if complete:
            self.finish(sid)
        return sid

    def stamp(self, sid):
        return (NOW-timedelta(seconds=10)+timedelta(milliseconds=len(self.paths[sid].read_text().splitlines()))).isoformat()

    def start_turn(self, sid):
        with self.paths[sid].open('a') as handle:
            handle.write(json.dumps({'type':'event_msg','timestamp':self.stamp(sid),'payload':{'type':'task_started'}})+'\n')

    def append(self, sid, kind, text):
        row = {'type':'event_msg','timestamp':self.stamp(sid),'payload':{
            'type':'item_completed','item':{'type':kind,'content':text}}}
        with self.paths[sid].open('a') as handle:
            handle.write(json.dumps(row)+'\n')
        os.utime(self.paths[sid], (NOW.timestamp(),NOW.timestamp()))

    def finish(self, sid):
        with self.paths[sid].open('a') as handle:
            handle.write(json.dumps({'type':'event_msg','timestamp':self.stamp(sid),
                'payload':{'type':'task_complete'}})+'\n')
        os.utime(self.paths[sid], (NOW.timestamp(),NOW.timestamp()))

    def next_turn(self, sid, instruction='Continue the same task.', final='Done.'):
        self.start_turn(sid)
        self.append(sid,'UserMessage',instruction)
        self.append(sid,'AgentMessage',final)
        self.finish(sid)

def verdict(kind='none', **changes):
    value = {'label':'DONE','kind':kind,'kind1_type':'','summary':'The export is complete.',
        'draft':'Synthetic reviewer draft.','question_for_user':'','escalate_reason':'',
        'checks':{check_name:'ok' for check_name in ('kiss','not_rebuilding','root_cause','in_scope','on_goal','honest')}}
    value.update(changes)
    return value

class PortAdapter:
    def run_pass(self, sandbox, verdicts=None, flags=()):
        import re
        from sidecarwatch import cli, profiles
        from tests.helpers import FakeClock, verdict as current_verdict
        sb=sandbox; outcome=Outcome(); verdicts=verdicts or {}
        clock=FakeClock(); clock.value=NOW
        state_dir=sb.root/'port-state'
        config_dir=sb.root/'port-config'
        (config_dir/'profiles').mkdir(parents=True,exist_ok=True)
        if hasattr(sb,'review_limit'):
            (config_dir/'config.toml').write_text('[reviewer]\nmax_per_run='+str(sb.review_limit)+'\n')
        from sidecarwatch.config import remember_consent
        remember_consent(config_dir/'config.toml')
        default={**profiles.DEFAULT,'goal':(sb.profiles/'_default.md').read_text()}
        if default['goal'].startswith('Already decided: '):
            default['decided']=[default['goal'].split(': ',1)[1]]
            default['goal']='Add CSV export.'
        for p in sb.profiles.glob('*.md'):
            if p.stem=='_default': continue
            text=p.read_text(); found=re.search(r'^match:\s*(.+)$',text,re.M)
            keys=found[1].split(',') if found else []
            (config_dir/'profiles'/(p.stem+'.toml')).write_text('name='+json.dumps(p.stem)+'\nmatch='+json.dumps(keys)+'\ngoal='+json.dumps(text)+'\n')
        class Reviewer:
            def review(self, system, block):
                sid=re.search(r'^session_id: (.+)$',block,re.M)[1]
                outcome.reviewed.append(sid)
                section=lambda begin,end:block.split(begin,1)[1].split(end,1)[0].strip()
                first=section('FIRST REQUEST:\n','\n\nLATEST INSTRUCTION:')
                latest=section('LATEST INSTRUCTION:\n','\n\nRECENT ACTIVITY')
                final=section('LAST MESSAGE OF THE FINISHED TURN:\n','\nDIGEST ')
                outcome.prompts.append((system+'\n'+section('<<<PROFILE ','PROFILE '),[{
                    'id':sid,'mission':first,'latest_instruction':first if latest=='(same as first request)' else latest,
                    'final_message':final,'cwd':re.search(r'^working_folder: (.*)$',block,re.M)[1]}]))
                value=verdicts.get(sid,verdict())
                if isinstance(value,Exception): raise value
                if value is None: return None
                templates={'ALREADY_DECIDED':'already_decided','REDIRECT_TO_GOAL':'back_to_goal','VERIFY_CLAIM':'show_evidence','HOUSEKEEPING':'housekeeping'}
                ident=templates.get(value.get('kind1_type'),'back_to_goal') if value['kind']=='1' else 'none'
                return current_verdict(label=value['label'],kind=value['kind'],template=ident,
                    decided_index=0 if ident=='already_decided' else -1,summary=value['summary'],draft=value['draft'],
                    question_for_user=value.get('question_for_user',''),checks={key:{'rating':'ok','note':''} for key in value['checks']})
        class Delivery:
            def available(self,session): return True
            def send(self,session,text):
                outcome.sent.append((session.id,text)); return '--fake-fail' not in flags
        env={'HOME':str(sb.synthetic_root),'SIDECARWATCH_CONFIG_DIR':str(config_dir),'SIDECARWATCH_STATE_DIR':str(state_dir),
             'SIDECARWATCH_CODEX_HOME':str(sb.codex),'SIDECARWATCH_CLAUDE_HOME':str(sb.synthetic_root/'claude'),
             'PATH':str(ROOT/'tests/fixtures/bin')+':/usr/bin:/bin','LANG':'C.UTF-8','FAKE_CLI_DIR':str(sb.synthetic_root/'fake')}
        args=['watch','--only',str(sb.synthetic_root)]
        if '--open' in flags: args=['open']
        elif '--resolve' in flags: args=['resolve',flags[flags.index('--resolve')+1]]
        else:
            if '--dry-run' in flags: args+=['--list-only']
            if '--init' in flags: args+=['--init']
            if '--no-send' not in flags: args+=['--auto-reply','--allow',str(sb.synthetic_root),'--live']
        out,err=io.StringIO(),io.StringIO()
        with patch.dict(os.environ,env,clear=True),patch.object(profiles,'DEFAULT',default):
            if '--live' in args and '--list-only' not in args and not (state_dir/'sends.jsonl').exists():
                assert cli.main(['rearm'],stdout=out,stderr=err,clock=clock)==0
            outcome.code=cli.main(args,stdout=out,stderr=err,clock=clock,reviewer=Reviewer(),delivery=Delivery())
        outcome.output=out.getvalue()
        if (state_dir/'state.json').exists():
            state=json.loads((state_dir/'state.json').read_text())
            outcome.needs_you={it['id']:it for it in state.get('needs',{}).values()}
            outcome.seen={key.split(':',1)[1]:value['handled'] for key,value in state['sessions'].items() if value['handled']}
        if (state_dir/'needs-you.log').exists(): outcome.alerts=(state_dir/'needs-you.log').read_text()
        return outcome
