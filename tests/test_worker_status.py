import json
import unittest
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, nudge, codex_session, claude_session


class WorkerStatusTests(unittest.TestCase):
    def test_audit_aborted_failed_and_interrupted_workers_remain_in_morning_status(self):
        for agent,case in (('codex','turn_aborted'),('codex','error'),('claude','interrupted')):
            with self.subTest(agent=agent,case=case), Sandbox() as sb:
                clock=FakeClock();sb.arm(clock)
                make=claude_session if agent=='claude' else codex_session
                path=make(getattr(sb,agent),sb.project,[('Goal','Done.')],at=clock.now())
                def halt(session,text):
                    stamp=clock.now().isoformat()
                    if agent=='codex':
                        rows=[{'type':'event_msg','timestamp':stamp,'payload':p} for p in
                              ({'type':'task_started'},{'type':'user_message','message':text},{'type':case})]
                    else:
                        rows=[{'type':'user','timestamp':stamp,'message':{'content':text}},
                              {'type':'assistant','timestamp':stamp,'message':{'content':[{'type':'tool_use','id':'c1','name':'Bash','input':{'command':'check'}}],'stop_reason':'tool_use'}},
                              {'type':'user','timestamp':stamp,'message':{'content':'[Request interrupted by user]'}}]
                    with path.open('a') as f: f.write(''.join(json.dumps(row)+'\n' for row in rows))
                d=FakeDelivery(halt);r=FakeReviewer([nudge(nudge_type='continue')])
                args=['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight','--interval','11m','--for','8h']
                code,out,err=sb.cli(args,clock=clock,reviewer=r,delivery=d)
                self.assertEqual(code,10,err)
                self.assertEqual((len(d.calls),len(r.calls)),(1,1))
                root=sb.root/'state/sidecarwatch'
                self.assertIn('worker-failed-or-interrupted',(root/'needs-you.md').read_text())
                records=[json.loads(l) for l in (root/'log.jsonl').read_text().splitlines()]
                self.assertEqual(sum(x['reason']=='worker-failed-or-interrupted' for x in records),1)
                self.assertEqual(sb.cli(['watch','--only',str(sb.project)],clock=clock,reviewer=FakeReviewer([]))[0],10)
                self.assertIn('worker-failed-or-interrupted',(root/'needs-you.md').read_text())

    def test_deadline_discovers_last_sleep_failures_and_running_workers(self):
        for case in ('error','running','no-response'):
            with self.subTest(case=case), Sandbox() as sb:
                clock=FakeClock();sb.arm(clock)
                path=codex_session(sb.codex,sb.project,[('Goal','Done.')],at=clock.now())
                d=FakeDelivery();r=FakeReviewer([nudge(nudge_type='continue')])
                initial=clock.now()
                def late():
                    if (clock.now()-initial).total_seconds()<60 or case=='no-response': return
                    rows=[{'type':'event_msg','timestamp':clock.now().isoformat(),'payload':{'type': 'error' if case=='error' else 'task_started'}}]
                    with path.open('a') as f:f.write(''.join(json.dumps(x)+'\n' for x in rows))
                    clock.on_sleep=None
                clock.on_sleep=late
                code,out,err=sb.cli(['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight','--interval','60s','--for','60s'],clock=clock,reviewer=r,delivery=d)
                self.assertEqual(code,10,err)
                self.assertEqual((len(d.calls),len(r.calls)),(1,1))
                expected={'error':'worker-failed-or-interrupted','running':'worker-unresolved-at-deadline','no-response':'awaiting-worker-at-deadline'}[case]
                self.assertIn(expected,(sb.root/'state/sidecarwatch/needs-you.md').read_text())
                self.assertIn('Stopped: deadline reached.',out)

    def test_late_unknown_approval_after_handled_turn_is_visible_without_review(self):
        for agent in ('claude','codex'):
            with self.subTest(agent=agent), Sandbox() as sb:
                clock=FakeClock();sb.arm(clock)
                make=claude_session if agent=='claude' else codex_session
                path=make(getattr(sb,agent),sb.project,[('Goal','Done.')],at=clock.now())
                d=FakeDelivery()
                args=['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live','--overnight']
                self.assertEqual(sb.cli(args,clock=clock,reviewer=FakeReviewer([nudge(nudge_type='continue')]),delivery=d)[0],0)
                row=({'type':'event_msg','payload':{'type':'approval_requested'}} if agent=='codex' else {'type':'system','subtype':'approval_requested'})
                row['timestamp']=clock.now().isoformat()
                with path.open('a') as f:f.write(json.dumps(row)+'\n')
                r=FakeReviewer([])
                code,out,err=sb.cli(args,clock=clock,reviewer=r,delivery=d)
                self.assertEqual(code,10,err)
                self.assertFalse(r.calls)
                self.assertEqual(len(d.calls),1)
                self.assertIn('unsupported-turn-data',(sb.root/'state/sidecarwatch/needs-you.md').read_text())
