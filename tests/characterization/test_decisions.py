import json
import unittest
from .adapter import PortAdapter, Sandbox, verdict
from .cases import CASES

class CommonCases:
    def check_case(self, case):
        with Sandbox() as sb:
            adapter = self.ADAPTER()
            run = lambda values=None, flags=(): adapter.run_pass(sb,values,flags)
            sid = sb.session(complete=case!='K1',subagent=case=='K5')
            if case in ('K1','K5'):
                out = run(); self.assertEqual(out.reviewed,[]); self.assertEqual(out.sent,[])
            elif case == 'K2':
                self.assertEqual(run().reviewed,[sid]); self.assertEqual(run().reviewed,[])
            elif case == 'K3':
                self.assertEqual(run(flags=['--init']).reviewed,[])
                self.assertEqual(run().reviewed,[])
                sb.next_turn(sid); self.assertEqual(run().reviewed,[sid])
            elif case == 'K4':
                out=run(flags=['--dry-run']); self.assertEqual(out.reviewed,[])
                self.assertFalse((sb.root/'port-state').exists())
            elif case in ('K6','K7'):
                if case == 'K6': sb.next_turn(sid,'Use the csv module.','Export tests passed.')
                else: sb.append(sid,'AgentMessage','Still working on the next step.')
                out=run(); data=out.prompts[0][1][0]
                self.assertEqual(data['mission'],'Add CSV export to the reading list.')
                self.assertEqual(data['latest_instruction'],'Use the csv module.' if case=='K6' else data['mission'])
                self.assertEqual(data['final_message'],'Export tests passed.' if case=='K6' else 'Done.')
            elif case == 'K8':
                (sb.profiles/'bookshelf.md').write_text('match: bookshelf\nSelected profile.\n')
                sb.session(2,name='bookshelf worker',cwd=str(sb.synthetic_root/'elsewhere'))
                sb.session(3,cwd=str(sb.synthetic_root/'other'))
                out=run(); self.assertEqual(len(out.prompts),3 if self.ADAPTER is PortAdapter else 2)
                matched=[item for system,payload in out.prompts if 'Selected profile.' in system for item in payload]
                self.assertEqual(len(matched),2)
                self.assertTrue(any('Default synthetic profile.' in system for system,_ in out.prompts))
            elif case == 'K9':
                self.assertTrue(run().prompts[0][1][0]['cwd'].startswith('~/'))
            elif case in ('K10','K11','K22','K24'):
                draft='Use the standard csv module; add no new dependency.' if case=='K24' else 'Synthetic reviewer draft.'
                value=verdict('1',draft=draft,kind1_type='ALREADY_DECIDED' if case=='K24' else 'REDIRECT_TO_GOAL')
                if case=='K24':
                    (sb.profiles/'_default.md').write_text('Already decided: '+draft)
                    sb.next_turn(sid,'Use existing directions.','Should I use the csv module?')
                out=run({sid:value},['--no-send'] if case=='K11' else ['--fake-fail'] if case=='K22' else [])
                self.assertEqual(len(out.sent),0 if case=='K11' else 1)
                if out.sent:
                    self.assertTrue(out.sent[0][1].startswith('From sidecarwatch'))
                    self.assertIn(draft,out.sent[0][1])
                if case=='K22': self.assertTrue(out.alerts); self.assertEqual(run({sid:value}).sent,[])
            elif case in ('K12','K14','K15','K16'):
                out=run({sid:verdict('2',question_for_user='Which output format?')})
                self.assertEqual(out.sent,[]); self.assertEqual(out.needs_you[sid]['question'],'Which output format?')
                self.assertEqual(out.needs_you[sid]['draft'],'Synthetic reviewer draft.')
                if case=='K14':
                    sb.next_turn(sid); self.assertEqual(run().needs_you,{})
                if case=='K15':
                    path=sb.root/'state'/'open.json'
                    saved=out.needs_you; saved[sid]['since']-=60; path.write_text(json.dumps(saved))
                    sb.next_turn(sid); again=run({sid:verdict('2')})
                    self.assertEqual(again.needs_you[sid]['since'],saved[sid]['since']); self.assertEqual(len(again.needs_you),1)
                if case=='K16':
                    self.assertEqual(run(flags=['--resolve','Example worker 1']).needs_you,{})
                    self.assertIn('0 open',run(flags=['--open']).output)
            elif case == 'K13':
                out=run(); self.assertEqual(out.needs_you,{}); self.assertEqual(out.sent,[]); self.assertIn(sid,out.seen)
            elif case in ('K17','K18','K19'):
                if case=='K17': sb.next_turn(sid,'From sidecarwatch: stay on scope.','How should I proceed?')
                if case=='K18':
                    run({sid:verdict('1')}); sb.next_turn(sid)
                out=run({sid:verdict('1',label='UNSURE' if case=='K19' else 'WORKING')})
                self.assertEqual(len(out.escalated),1); self.assertEqual(out.sent,[]); self.assertIn(sid,out.needs_you)
            elif case=='K20':
                ids=[sid]+[sb.session(i) for i in range(2,7)]
                out=run({s:verdict('1',label='UNSURE') for s in ids})
                self.assertEqual(len(out.escalated),5); self.assertEqual(len(out.needs_you),6); self.assertEqual(out.sent,[])
            elif case=='K21':
                out=run({sid:None}); self.assertNotIn(sid,out.seen); self.assertTrue(out.alerts)
                self.assertEqual(run({sid:None}).reviewed,[sid])
            elif case=='K23':
                out=run({sid:RuntimeError('fake reviewer failed')}); self.assertEqual(out.error,'RuntimeError')
                self.assertEqual(out.sent,[]); self.assertNotIn(sid,out.seen)

def make_case(case):
    def test(self): self.check_case(case)
    return test

class PortDecisions(unittest.TestCase):
    ADAPTER = PortAdapter

    def check_case(self, case):
        if case not in ('K7','K10','K15','K17','K18','K19','K20','K21','K22','K23','K24'):
            return CommonCases.check_case(self,case)
        with Sandbox() as sb:
            adapter=PortAdapter(); sid=sb.session()
            run=lambda values=None,flags=():adapter.run_pass(sb,values,flags)
            if case=='K7':
                sb.append(sid,'AgentMessage','Still working.')
                out=run(); self.assertEqual(out.reviewed,[]); self.assertEqual(out.sent,[])
            elif case in ('K10','K22'):
                out=run({sid:verdict('1')},['--fake-fail'] if case=='K22' else [])
                self.assertEqual(len(out.sent),1)
                self.assertTrue(out.sent[0][1].startswith('[AUTOMATED sidecarwatch reply '))
                self.assertNotIn('Synthetic reviewer draft.',out.sent[0][1])
                if case=='K22':
                    self.assertEqual(out.code,5); self.assertEqual(run({sid:verdict('1')}).sent,[])
            elif case in ('K17','K24'):
                if case=='K17':
                    from sidecarwatch.policy import label, TEMPLATES
                    from sidecarwatch.sessions import Session
                    from pathlib import Path
                    instruction=label(Session('codex',sid,Path('.')),TEMPLATES['back_to_goal'],'claude',5)
                else:
                    instruction='Use existing directions.'
                    (sb.profiles/'_default.md').write_text('Already decided: Use the standard csv module.')
                sb.next_turn(sid,instruction,'Should I use the csv module?')
                out=run({sid:verdict('1',kind1_type='ALREADY_DECIDED')})
                self.assertEqual(out.sent,[]); self.assertIn(sid,out.needs_you); self.assertEqual(out.code,10)
            elif case=='K18':
                self.assertEqual(len(run({sid:verdict('1')}).sent),1)
                sb.next_turn(sid,final='Tests passed.')
                out=run({sid:verdict('1')})
                self.assertEqual(out.sent,[]); self.assertEqual(out.escalated,[])
                self.assertIn('cooldown',out.output)
            elif case=='K15':
                out=run({sid:verdict('2')}); before=out.needs_you[sid]['since']
                sb.next_turn(sid,final='Tests passed.')
                out=run({sid:verdict('2')})
                self.assertEqual(len(out.needs_you),1); self.assertEqual(out.needs_you[sid]['since'],before)
            elif case in ('K19','K20'):
                if case=='K20': sb.review_limit=5
                ids=[sid]+([sb.session(i) for i in range(2,7)] if case=='K20' else [])
                out=run({key:verdict('1',label='UNSURE') for key in ids})
                self.assertEqual(out.sent,[]); self.assertEqual(len(out.needs_you),len(ids)); self.assertEqual(out.code,10)
                if case=='K20': self.assertEqual(len(out.reviewed),5)
            elif case=='K21':
                for attempt in range(3):
                    out=run({sid:None}); self.assertEqual(out.reviewed,[sid]); self.assertEqual(out.code,5)
                    self.assertEqual(sid in out.seen,attempt==2)
                self.assertEqual(run({sid:None}).reviewed,[])
            elif case=='K23':
                other=sb.session(2)
                out=run({sid:RuntimeError('fake reviewer failed')})
                self.assertEqual(out.code,5); self.assertEqual(set(out.reviewed),{sid,other})
                self.assertNotIn(sid,out.seen); self.assertIn(other,out.seen); self.assertEqual(out.sent,[])

for key,title,original,port in CASES:
    method=make_case(key); method.__doc__=title
    setattr(PortDecisions,'test_'+key,method)
