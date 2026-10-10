import json
import unittest
from sidecarwatch.policy import label, TEMPLATES
from sidecarwatch.sessions import Session
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, codex_session, claude_session


class InvalidStringTests(unittest.TestCase):
    def test_malformed_decoded_strings_skip_visibly_and_never_deliver(self):
        cases=(('final','\ud800'),('final','\udfff'),('user','before\x00after'),
               ('final','z'*300000),('cwd','bad\x00path'),('cwd','bad\ud800path'),
               ('progress','\ud800'),('progress','\x00'),('progress','x'*300000))
        for agent,shape in (('claude',''),('codex','A'),('codex','B')):
            for location,value in cases:
                with self.subTest(agent=agent,shape=shape,location=location,size=len(value)), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    make=claude_session if agent=='claude' else codex_session
                    opts={'shape':shape} if shape else {}
                    user=value if location=='user' else 'Readable goal'
                    final=value if location=='final' else 'Done.'
                    path=make(getattr(sb,agent),sb.project,[(user,final)],at=clock.now(),**opts)
                    rows=[json.loads(l) for l in path.read_text().splitlines()]
                    if location=='user' and agent=='claude':
                        earlier = dict(rows[0], message={'content':'Readable earlier goal'})
                        rows.insert(0,earlier)
                    if location=='cwd':
                        # Keep a readable scope association around the damaged row.
                        rows.insert(-1,{'type':'turn_context','timestamp':clock.now().isoformat(),'payload':{'cwd':value}})
                    if location=='progress': rows.append({'type':'progress','data':value})
                    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    for index, args in enumerate((['sessions'],['show',agent],['audit',agent,'--list-only'],['doctor','--shapes'],['watch','--only',str(sb.project),'--list-only'])):
                        code,out,err=sb.cli(args,clock=clock)
                        self.assertNotEqual(code,1,err)
                        if index == 0:
                            self.assertIn('skipped malformed or oversized', err)
                            self.assertIn(str(path), err)
                        else:
                            if args[0] == 'audit':
                                self.assertEqual(code, 8)
                                self.assertIn('deliberate privacy refusal', err)
                            else:
                                self.assertEqual(err, '')
                        self.assertNotIn('UnicodeEncodeError',out+err)
                    d=FakeDelivery();r=FakeReviewer([])
                    code,out,err=sb.cli(['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live'],clock=clock,reviewer=r,delivery=d)
                    self.assertEqual(code,10,err)
                    self.assertFalse(d.calls or r.calls)
                    self.assertIn('incomplete-transcript',(sb.root/'state/sidecarwatch/needs-you.md').read_text())

    def test_five_thousand_digit_marker_counters_do_not_convert(self):
        for agent in ('claude','codex'):
            for numerator in (True,False):
                with self.subTest(agent=agent,numerator=numerator), Sandbox() as sb:
                    clock=FakeClock();sb.arm(clock)
                    make=claude_session if agent=='claude' else codex_session
                    path=make(getattr(sb,agent),sb.project,[('Goal','Done.')],at=clock.now())
                    frame=label(Session(agent,'sample',path),TEMPLATES['continue'],'claude',5)
                    frame=frame.replace('1/5','1'*5000+'/5' if numerator else '1/'+'5'*5000)
                    with path.open('a') as f: f.write(json.dumps({'type':'progress','payload':frame})+'\n')
                    r=FakeReviewer([]);d=FakeDelivery()
                    code,out,err=sb.cli(['watch','--only',str(sb.project),'--allow',str(sb.project),'--auto-reply','--live'],clock=clock,reviewer=r,delivery=d)
                    self.assertEqual(code,10,err)
                    self.assertIn('skipped malformed marker counters',err)
                    self.assertFalse(r.calls or d.calls)

    def test_malformed_index_names_are_skipped_with_note(self):
        for bad in ('\ud800','\x00','x'*300000):
            with self.subTest(size=len(bad)), Sandbox() as sb:
                codex_session(sb.codex,sb.project,[('Readable goal','Done.')])
                index=sb.codex/'session_index.jsonl'
                index.write_text(json.dumps({'id':'sample','thread_name':bad})+'\n')
                for number, args in enumerate((['sessions'],['watch','--only',str(sb.project),'--list-only'])):
                    code,out,err=sb.cli(args)
                    self.assertEqual(code,0,err)
                    if number == 0:
                        self.assertIn(str(index)+': line 1: skipped', err)
                    else:
                        self.assertEqual(err, '')
                    self.assertIn('Readable goal',out)
