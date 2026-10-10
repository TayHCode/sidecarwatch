import copy
import json
import unittest
from sidecarwatch.turnstate import clean_completion
from sidecarwatch.codex_format import parse as dp
from sidecarwatch.claude_format import parse as cp
from sidecarwatch.policy import TEMPLATES, Options, decide
from sidecarwatch.config import DEFAULTS, merge
from sidecarwatch.state import State
from sidecarwatch.reviewer import validate
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, nudge, codex_session, claude_session, write_lines, ordered
from tests.audit3_cases import FINALS, STRUCTURES, modify
from tests.turn_scenarios import SCENARIOS, seed, scenario

FORMATS = (('claude','',cp), ('codex','A',dp), ('codex','B',dp))


class WholeTurnTests(unittest.TestCase):
    def trial(self, sb, clock, agent, shape, parse, rows, expected=False):
        make = claude_session if agent=='claude' else codex_session
        path = make(getattr(sb,agent),sb.project,[],at=clock.now())
        write_lines(path,rows)
        s=parse(path,clock.now(),60); s.folders={str(sb.project)}
        self.assertEqual(clean_completion(s), expected)
        state=State(sb.root/'state/sidecarwatch',clock)
        for template in TEMPLATES:
            result=decide(s,validate(nudge(nudge_type=template,decided_index=0 if template=='already_decided' else -1),profile={'decided':['Use the standard csv module.']}),state,merge(DEFAULTS,{}),
                          Options(True,[str(sb.project)],True,True),clock.now(),fresh=lambda:s,available=True)
            self.assertEqual(result.reason=='send',expected,(template,result.reason))
        d=FakeDelivery(); r=FakeReviewer([nudge(nudge_type='continue')])
        code,out,err=sb.cli(['watch','--agent',agent,'--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight'],
                           clock=clock,reviewer=r,delivery=d)
        self.assertNotEqual(code,1,err)
        self.assertEqual(bool(d.calls),expected,(code,out,err))
        return path,s

    def test_forty_additional_scenarios_in_both_formats(self):
        self.assertEqual(len(SCENARIOS),40)
        for agent,shape,parse in FORMATS:
            for case in SCENARIOS:
                with self.subTest(agent=agent,shape=shape,case=case), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1' if agent=='claude' else 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
                    rows=scenario(agent,shape,sb.project,sid,case,clock.now())
                    self.trial(sb,clock,agent,shape,parse,rows)

    def test_auditor_twenty_structural_reproductions_exact_and_retimed(self):
        self.assertEqual(len(STRUCTURES),20)
        for agent,shape,parse in FORMATS:
            for case in STRUCTURES:
                for retime in (False,True):
                    with self.subTest(agent=agent,shape=shape,case=case,retime=retime), Sandbox() as sb:
                        clock=FakeClock();sb.arm(clock)
                        make=claude_session if agent=='claude' else codex_session
                        opts={'shape':shape} if shape else {}
                        path=make(getattr(sb,agent),sb.project,[('Check the current task.','Done.')],at=clock.now(),**opts)
                        rows=modify([json.loads(l) for l in path.read_text().splitlines()],case,agent)
                        if retime: ordered(rows,clock.now())
                        self.trial(sb,clock,agent,shape,parse,rows)

    def test_auditor_forty_final_messages_cannot_supply_missing_completion(self):
        self.assertEqual(len(FINALS),40)
        for agent,shape,parse in FORMATS:
            for case,final in FINALS:
                with self.subTest(agent=agent,shape=shape,case=case), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    make=claude_session if agent=='claude' else codex_session
                    opts={'shape':shape} if shape else {}
                    path=make(getattr(sb,agent),sb.project,[('Goal',final)],at=clock.now(),**opts)
                    rows=[json.loads(line) for line in path.read_text().splitlines()]
                    if agent=='claude': rows[-1]['message'].pop('stop_reason')
                    else: rows=[row for row in rows if row.get('payload',{}).get('type')!='task_complete']
                    self.trial(sb,clock,agent,shape,parse,rows)

    def test_clean_structured_controls_and_resumed_new_human_turn(self):
        for agent,shape,parse in FORMATS:
            for resume in (False,True):
                with self.subTest(agent=agent,shape=shape,resume=resume), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1' if agent=='claude' else 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
                    rows=seed(agent,shape,sb.project,sid)
                    if resume:
                        failed=scenario(agent,shape,sb.project,sid,'negative-exit',clock.now())
                        rows=failed+rows[0 if agent=='claude' else 1:]
                    ordered(rows,clock.now())
                    self.trial(sb,clock,agent,shape,parse,rows,True)

    def test_every_event_checked_before_activity_cut_and_after_completion(self):
        for agent,shape,parse in FORMATS:
            with self.subTest(agent=agent,shape=shape), Sandbox() as sb:
                clock=FakeClock();sb.arm(clock)
                sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1' if agent=='claude' else 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
                rows=scenario(agent,shape,sb.project,sid,'negative-exit',clock.now())
                # Push the original failed tool far outside the fifteen-event digest.
                for i in range(30):
                    if agent=='claude': row={'type':'assistant','message':{'content':'Checking local output.','stop_reason':None}}
                    else: row={'type':'event_msg','payload':{'type':'agent_message','message':'Checking local output.'}}
                    rows.insert(-1,row)
                ordered(rows,clock.now())
                self.trial(sb,clock,agent,shape,parse,rows)

    def test_delivery_rechecks_whole_file_after_review_and_capability_probe(self):
        for agent,shape,parse in FORMATS:
            for phase in ('review','probe'):
                with self.subTest(agent=agent,shape=shape,phase=phase), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    make=claude_session if agent=='claude' else codex_session
                    opts={'shape':shape} if shape else {}
                    path=make(getattr(sb,agent),sb.project,[('Goal','Added CSV export.')],at=clock.now(),**opts)
                    def damage():
                        with path.open('a') as stream:
                            stream.write(json.dumps({'type':'error','timestamp':clock.now().isoformat(),'error':'late failure'})+'\n')
                    def review():
                        if phase=='review': damage()
                        return nudge(nudge_type='continue')
                    class Delivery(FakeDelivery):
                        def available(self,session):
                            if phase=='probe': damage()
                            return True
                    d=Delivery()
                    code,out,err=sb.cli(['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight'],clock=clock,reviewer=FakeReviewer([review]),delivery=d)
                    self.assertEqual(code,10,err)
                    self.assertFalse(d.calls)
                    self.assertIn('stale',out)

    def test_conflicting_identifiers_unknown_fields_and_terminal_metadata(self):
        for agent,shape,parse in FORMATS:
            for case in ('unknown-tool-block','unknown-text-field','foreign-activity','null-error-flag','terminal-in-progress','identity-change','turn-id-mismatch','pending-subagent','tool-in-progress'):
                with self.subTest(agent=agent,shape=shape,case=case), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    sid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1' if agent=='claude' else 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
                    rows=seed(agent,shape,sb.project,sid)
                    if case=='unknown-text-field':
                        if agent=='claude': rows[0]['message']['content']=[{'type':'text','text':'Goal','future_flags':True}]
                        else: rows[2]={'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Goal','future_flags':True}]}}
                    elif case=='foreign-activity':
                        if agent=='claude': rows[-1]['isSidechain']=True
                        else: rows[-1]['payload']['future_source']='another-worker'
                    elif case=='unknown-tool-block':
                        if agent=='claude': rows[2]['message']['content'][0]['content']=[{'type':'future_output','text':'success'}]
                        else: rows[4]['payload']['output']={'future_output':'success'}
                    elif case=='null-error-flag': rows[-1]['is_error']=None
                    elif case=='terminal-in-progress': rows[-1]['status']='in_progress'
                    elif case in ('pending-subagent','tool-in-progress'):
                        output = rows[2]['message']['content'][0] if agent=='claude' else rows[4]['payload']
                        if case=='pending-subagent': output['children']=[{'status':'in_progress'}]
                        else: output['status']='in_progress'
                    elif case=='identity-change':
                        if agent=='claude': rows[0]['sessionId']=sid;rows[-1]['sessionId']='different'
                        else: rows.insert(-1,dict(type='session_meta',payload={'id':'different','cwd':str(sb.project)}))
                    else:
                        if agent=='claude': rows[-1]['message']['role']='user'
                        else: rows[1]['payload']['turn_id']='first';rows[-1]['payload']['turn_id']='second'
                    ordered(rows,clock.now())
                    self.trial(sb,clock,agent,shape,parse,rows)

    def test_audit_minimal_cases_through_real_adapters_using_only_fake_executables(self):
        from tests.audit3_adapters import CASES
        self.assertEqual(len(CASES),6)
        for case,original in CASES:
            with self.subTest(case=case), Sandbox() as sb:
                clock=FakeClock();sb.arm(clock)
                agent='claude' if case.startswith('claude') else 'codex'
                make=claude_session if agent=='claude' else codex_session
                path=make(getattr(sb,agent),sb.project,[],at=clock.now())
                rows=json.loads(json.dumps(original).replace('<synthetic-project>',str(sb.project)))
                write_lines(path,ordered(rows,clock.now()))
                cfg=sb.root/'c.toml';cfg.write_text('[delivery]\nclaude_resume=true\n')
                code,out,err=sb.cli(['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight','--config',str(cfg)],clock=clock,reviewer=FakeReviewer([nudge(nudge_type='continue')]))
                self.assertIn(code,(5,10),err)
                calls=sb.fake/'calls.jsonl'
                if calls.exists():
                    for row in map(json.loads,calls.read_text().splitlines()):
                        self.assertNotIn('--resume',row['argv'])
                        self.assertNotIn('--message',row['argv'])
