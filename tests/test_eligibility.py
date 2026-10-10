import json
import unittest
from unittest.mock import patch
from sidecarwatch import config, ledger, templates
from sidecarwatch.reviewer import validate
from sidecarwatch.state import State
from tests.helpers import Sandbox, FakeReviewer, FakeDelivery, FakeClock, nudge, codex_session, claude_session, write_lines

class EligibilityTests(unittest.TestCase):
    def trial(self, agent, shape, final, *, completed=True, template='continue', quote='Use the standard csv module.', index=0, changed=False, unknown=False):
        with Sandbox() as sb:
            clock=FakeClock(); sb.arm(clock)
            make=claude_session if agent=='claude' else codex_session
            opts={'shape':shape} if shape else {}
            path=make(getattr(sb,agent),sb.project,[('Add CSV export.',final)],at=clock.now(),**opts)
            if not completed:
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                if agent=='claude': rows[-1]['message'].pop('stop_reason')
                else: rows=[r for r in rows if r.get('payload',{}).get('type')!='task_complete']
                write_lines(path,rows)
            if unknown:
                with path.open('a') as f: f.write(json.dumps({'type':'future_shape','timestamp':clock.now().isoformat()})+'\n')
            profile=sb.root/'config/sidecarwatch/profiles/books.toml'; profile.parent.mkdir(parents=True)
            profile.write_text('name="books"\npaths=['+json.dumps(str(sb.project))+']\ndecided=['+json.dumps(quote)+']\n')
            def review():
                if changed: profile.write_text(profile.read_text().replace('csv','json'))
                return nudge(template=template,decided_index=index if template=='already_decided' else -1,
                             draft='Reviewer prose is never an automatic message.')
            d=FakeDelivery()
            code,out,err=sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live','--overnight'],
                               clock=clock,reviewer=FakeReviewer([review]),delivery=d)
            self.assertNotEqual(code,1,err)
            return code,out,d.calls

    def test_status_text_never_establishes_completion(self):
        for agent,shape in [('claude',''),('codex','A'),('codex','B')]:
            for status in ('ready','done','needs-user','blocked','READY','ready\nMore prose.','ready\n```'):
                with self.subTest(agent=agent,shape=shape,status=status):
                    _,_,calls=self.trial(agent,shape,'TURN-STATUS: '+status,completed=False)
                    self.assertEqual(calls,[])
            for text in ('Done.\nTURN-STATUS: needs-user','Done.\nTURN-STATUS: blocked','Done.\nError: failed test.\nTURN-STATUS: ready'):
                self.assertEqual(self.trial(agent,shape,text)[2],[])
            self.assertEqual(len(self.trial(agent,shape,'Added CSV export.\nTURN-STATUS: ready')[2]),1)
            self.assertEqual(self.trial(agent,shape,'Done.\nTURN-STATUS: ready',unknown=True)[2],[])

    def test_question_failure_hard_wording_and_language_flag_every_class(self):
        for text in ('Should I proceed?','Done. The test failed.','Done. Deploy the build.','完了しました。'):
            for template in templates.TEMPLATES:
                self.assertEqual(self.trial('codex','B',text,template=template)[2],[])

    def test_decision_quote_boundary_and_freshness(self):
        code,out,calls=self.trial('codex','A','Added CSV export.',template='already_decided')
        self.assertEqual(code,0,out); self.assertEqual(len(calls),1)
        body=calls[0][1].splitlines()[1]
        self.assertIn('"Use the standard csv module."',body)
        self.assertNotIn('Reviewer prose',body)
        for quote in ('','two\nlines','non-ASCII é','contains "quote"','[AUTOMATED sidecarwatch reply ','Pay the bill.'):
            self.assertEqual(self.trial('codex','A','Done.',template='already_decided',quote=quote)[2],[])
        self.assertEqual(self.trial('codex','A','Done.',template='already_decided',index=1)[2],[])
        self.assertEqual(self.trial('codex','A','Done.',template='already_decided',changed=True)[2],[])
        self.assertEqual(self.trial('codex','A','Should I use csv?',template='already_decided')[2],[])

    def test_default_injection_off_and_witness_name(self):
        cfg=config.merge(config.DEFAULTS,{})
        self.assertFalse(cfg['delivery']['codex_queue']); self.assertFalse(cfg['delivery']['claude_resume'])
        with Sandbox() as sb, patch.object(ledger,'APP_NAME','example-tool'):
            state=State(sb.root/'state')
            self.assertIn('/example-tool/accounting/',str(state.send_log.witness))

    def test_quote_index_type_and_all_required_fields(self):
        from sidecarwatch.reviewer import InvalidVerdict
        for value in (True,1.0,'0',None):
            with self.assertRaises(InvalidVerdict): validate(nudge(template='already_decided',decided_index=value))
        for key in nudge():
            value=nudge(); value.pop(key)
            with self.assertRaises(InvalidVerdict): validate(value)
