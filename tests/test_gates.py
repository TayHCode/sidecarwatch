import datetime as dt
import unittest
from sidecarwatch.config import merge, DEFAULTS
from sidecarwatch.policy import Options, decide, label, CONTINUE, CLOSING
from sidecarwatch.reviewer import validate
from sidecarwatch.sessions import folders
from sidecarwatch.claude_format import parse
from sidecarwatch.state import State
from tests.helpers import Sandbox, claude_session, FakeClock, nudge


class GateTests(unittest.TestCase):
    def setUp(self):
        self.sb=Sandbox().__enter__()
        self.addCleanup(self.sb.__exit__,None,None,None)
        self.clock=FakeClock(); self.cfg=merge(DEFAULTS,{})
        self.path=claude_session(self.sb.claude,self.sb.project,[('Add CSV export','Completed successfully.')],at=self.clock.now())
        self.s=parse(self.path,self.clock.now(),60); self.s.folders=folders(self.s.cwd,{})
        self.state=State(self.sb.root/'own', self.clock)
        self.state.send_log.arm(self.clock.now())
        self.opts=Options(True,[str(self.sb.project)],False,True)

    def choice(self, value=None, **kwargs):
        return decide(self.s,validate(value or nudge()),self.state,self.cfg,self.opts,self.clock.now(),fresh=kwargs.get('fresh',lambda:self.s),available=kwargs.get('available',True))

    def test_no_send_without_auto_reply(self):
        self.opts.auto_reply=False
        self.assertEqual(self.choice().reason,'auto-reply-off')

    def test_dry_run_is_default(self):
        self.opts.live=False
        self.assertEqual(self.choice().reason,'dry-run')

    def test_allow_list(self):
        self.opts.allow=[]
        self.assertEqual(self.choice().reason,'not-allowed')

    def test_never_from_verdict(self):
        for value,reason in [(nudge(reply_kind='needs_user'),'needs-user'),(nudge(needs_user_topics=['decision']),'inconsistent-verdict'),(nudge(confidence='low'),'low-confidence')]:
            d=self.choice(value)
            self.assertEqual(d.reason,reason)
            self.assertTrue(d.needs_user)

    def test_continue_fixed_text_and_label(self):
        value=nudge(nudge_type='continue')
        self.assertEqual(self.choice(value).reason,'continue-not-enabled')
        self.opts.overnight=True
        self.assertEqual(self.choice(value).body,CONTINUE)
        self.assertEqual(self.choice(value).reason,'send')
        message=label(self.s,CONTINUE,'codex',5)
        self.assertTrue(message.startswith('[AUTOMATED sidecarwatch reply 1/5 |'))
        self.assertTrue(message.endswith(CLOSING))
        self.assertLessEqual(len(message),1000)
        self.assertTrue(message.isascii())
        self.s.final_message='Should I continue?'
        self.assertEqual(self.choice(value).reason,'never:decision')
        self.opts.overnight=False
        self.assertEqual(self.choice().reason,'never:decision')

    def test_running_stale_handled(self):
        self.s.state='running'
        self.assertEqual(self.choice().reason,'not-idle')
        self.s.state='idle'
        self.assertEqual(self.choice(fresh=lambda:None).reason,'stale')
        self.state.record(self.s,self.clock.now())
        self.assertEqual(self.choice().reason,'already-handled')

    def test_rates_and_budgets(self):
        row=self.state.session(self.s); now=self.clock.now().timestamp()
        row['sends']=[now-1]
        self.assertEqual(self.choice().reason,'cooldown')
        row['sends']=[now-200]*6
        self.assertEqual(self.choice().reason,'rate-limit')
        row['sends']=[]
        self.state.data['folders'][str(self.sb.project)]=[now-200]*30
        self.s.id='bbbbbbbb-bbbb-4bbb-8bbb-aaaaaaaaaaa3'
        self.assertEqual(self.choice().reason,'folder-limit')
        self.state.data['folders']={}; self.opts.sent=40
        self.assertEqual(self.choice().reason,'run-budget')
        self.opts.sent=0; self.opts.deadline=self.clock.now()-dt.timedelta(seconds=1)
        self.assertEqual(self.choice().reason,'deadline')
        self.opts.deadline=None
        self.assertEqual(self.choice(available=False).reason,'no-injection')
