from tests.helpers import text_parts
import json
import unittest
from sidecarwatch.claude_format import parse as cp
from sidecarwatch.codex_format import parse as dp
from sidecarwatch.config import merge, DEFAULTS, FORBIDDEN_ARGS
from sidecarwatch.deliver import Delivery
from sidecarwatch.policy import label, TEMPLATES
from sidecarwatch.state import State
from tests.helpers import Sandbox, FakeClock, codex_session, claude_session, FakeReviewer, nudge


class DeliveryTests(unittest.TestCase):
    def test_unavailable_delivery_prints_and_bypass_args_fail(self):
        for agent in ['claude','codex']:
            with self.subTest(agent=agent), Sandbox() as sb:
                sb.arm()
                (claude_session if agent=='claude' else codex_session)(getattr(sb,agent),sb.project,[('Goal','Done')])
                if agent == 'codex':
                    (sb.fake/'old-help').touch()
                args=['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live']
                code,out,err=sb.cli(args,reviewer=FakeReviewer([nudge()]))
                self.assertEqual(code,0,err)
                self.assertIn('no-injection',out)
                self.assertIn('Nothing was sent.',out)
                calls=[json.loads(line) for line in (sb.fake/'calls.jsonl').read_text().splitlines()]
                self.assertTrue(all('--help' in call['argv'] for call in calls))
        with Sandbox() as sb:
            cfg=sb.root/'cfg.toml'
            for flag in FORBIDDEN_ARGS:
                for table in ['delivery.claude','reviewer.claude','reviewer.codex']:
                    cfg.write_text('['+table+']\nextra_args='+json.dumps([flag])+'\n')
                    self.assertEqual(sb.cli(['doctor','--config',str(cfg)])[0],2)
            self.assertFalse((sb.fake/'calls.jsonl').exists())

    def test_delivery_codex_queue(self):
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[('Goal','Done')])
            s=dp(p,FakeClock().now(),60)
            state=State(sb.root/'own'); cfg=merge(DEFAULTS,{'delivery':{'codex_queue':True}})
            d=Delivery(cfg,state)
            self.assertTrue(d.available(s))
            text=label(s,TEMPLATES['back_to_goal'],'claude',5)
            self.assertTrue(d.send(s,text))
            row=json.loads((sb.fake/'calls.jsonl').read_text().splitlines()[-1])
            self.assertEqual(row['argv'],['queue','--thread',s.id,'--message',text])
            self.assertEqual(row['active'],'1'); self.assertFalse(row[text_parts('had_api_ke', 'y')])
            (sb.fake/'old-help').touch()
            self.assertFalse(Delivery(cfg,state).available(s))
            (sb.fake/'exit-code').write_text('2')
            self.assertFalse(d.send(s,text))
            calls_before = (sb.fake/'calls.jsonl').read_text()
            s.state = 'running'
            self.assertFalse(d.send(s,text))
            self.assertEqual((sb.fake/'calls.jsonl').read_text(), calls_before)

    def test_delivery_claude_resume_opt_in(self):
        with Sandbox() as sb:
            p=claude_session(sb.claude,sb.project,[('Goal','Done')])
            s=cp(p,FakeClock().now(),60); cfg=merge(DEFAULTS,{})
            state=State(sb.root/'own'); d=Delivery(cfg,state)
            self.assertFalse(d.available(s))
            cfg['delivery']['claude_resume']=True
            self.assertTrue(d.available(s))
            text=label(s,TEMPLATES['back_to_goal'],'codex',5)
            self.assertTrue(d.send(s,text))
            row=json.loads((sb.fake/'calls.jsonl').read_text().splitlines()[-1])
            self.assertEqual(row['argv'],['-p','--resume',s.id])
            self.assertEqual(row['stdin'],text)
            self.assertEqual(row['cwd'],str(sb.project))

    def test_no_injection_and_failed_turn_not_retried(self):
        with Sandbox() as sb:
            sb.arm()
            codex_session(sb.codex,sb.project,[('Goal','Done')])
            config=sb.root/'config/sidecarwatch/config.toml'
            config.parent.mkdir(parents=True,exist_ok=True)
            config.write_text('[delivery]\ncodex_queue=true\n')
            (sb.fake/'exit-code').write_text('3')
            args=['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live']
            fake=FakeReviewer([nudge()])
            sb.cli(args,reviewer=fake)
            sb.cli(args,reviewer=fake)
            calls=[json.loads(x) for x in (sb.fake/'calls.jsonl').read_text().splitlines()]
            self.assertEqual(len([x for x in calls if x['argv'][0]=='queue' and '--help' not in x['argv']]),1)
            log=(sb.root/'state/sidecarwatch/log.jsonl').read_text()
            self.assertIn('send-failed',log)
