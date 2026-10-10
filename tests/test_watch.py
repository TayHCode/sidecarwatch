from tests.helpers import text_parts
import json
import os
import stat
import unittest
from sidecarwatch.state import State
from tests.helpers import Sandbox, FakeClock, FakeDelivery, FakeReviewer, nudge, verdict, codex_session, claude_session, append_turn


class WatchTests(unittest.TestCase):
    def test_session_scope_and_explicit_off(self):
        with Sandbox() as sb:
            claude_session(sb.claude,sb.project,[('Other goal','Done')])
            codex_session(sb.codex,sb.project,[('Selected goal','Done')])
            fake, delivery = FakeReviewer([nudge()]), FakeDelivery()
            code,out,err=sb.cli(['watch','--session','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2','--no-auto-reply'],reviewer=fake,delivery=delivery)
            self.assertEqual(code,0,err)
            self.assertEqual(len(fake.calls),1)
            self.assertIn('Selected goal',fake.calls[0][1])
            self.assertNotIn('Other goal',fake.calls[0][1])
            self.assertEqual(delivery.calls,[])

    def test_verdict_blocks_are_marked_not_sent(self):
        cases = [(nudge(reply_kind='needs_user'),'needs-user'), (nudge(needs_user_topics=['decision']),'inconsistent-verdict'), (nudge(confidence='low'),'low-confidence')]
        for value,reason in cases:
            with self.subTest(reason=reason), Sandbox() as sb:
                codex_session(sb.codex,sb.project,[('Goal','Done')])
                delivery=FakeDelivery()
                code,out,err=sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live'],reviewer=FakeReviewer([value]),delivery=delivery)
                self.assertEqual(code,10,err)
                self.assertEqual(delivery.calls,[])
                note=(sb.root/'state/sidecarwatch/needs-you.md').read_text()
                self.assertIn('Needs you',note)
                self.assertIn('NOT SENT',note)
                self.assertIn(reason,note)

    def test_watch_pass_and_list_only(self):
        with Sandbox() as sb:
            p=codex_session(sb.codex,sb.project,[('Goal','Done')],shape='B')
            args=['watch','--only',str(sb.project)]
            fake=FakeReviewer([nudge()]); delivery=FakeDelivery()
            code,out,err=sb.cli(args+['--list-only'],reviewer=fake)
            self.assertEqual(code,0); self.assertEqual(fake.calls,[])
            self.assertFalse((sb.root/'state').exists())
            code,out,err=sb.cli(args,reviewer=fake,delivery=delivery)
            self.assertEqual(code,0,err)
            self.assertIn('1 reviewed',out); self.assertIn('auto-reply-off',(sb.root/'state/sidecarwatch/log.jsonl').read_text())
            self.assertEqual(delivery.calls,[])
            self.assertEqual(sb.cli(args,reviewer=fake)[0],0)
            self.assertEqual(len(fake.calls),1)
            self.assertEqual(sb.cli(['watch'])[0],2)

    def test_dry_run_allow_and_flag(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Goal','Done')],shape='B')
            base=['watch','--only',str(sb.project),'--auto-reply']
            self.assertEqual(sb.cli(base)[0],2)
            delivery=FakeDelivery()
            code,out,err=sb.cli(base+['--allow',str(sb.project)],reviewer=FakeReviewer([nudge()]),delivery=delivery)
            self.assertEqual(code,0,err); self.assertEqual(delivery.calls,[])
            self.assertIn('would-send',(sb.root/'state/sidecarwatch/log.jsonl').read_text())
            p=codex_session(sb.codex,sb.project,[('Goal','May I deploy?')],shape='B')
            code,out,err=sb.cli(base+['--allow',str(sb.project),'--live'],reviewer=FakeReviewer([nudge()]),delivery=delivery)
            self.assertEqual(code,10,err)
            self.assertIn('NOT SENT',(sb.root/'state/sidecarwatch/needs-you.md').read_text())

    def test_stale_review_not_sent(self):
        with Sandbox() as sb:
            sb.arm()
            p=claude_session(sb.claude,sb.project,[('Goal','Done')])
            def changed():
                with p.open('a') as f:
                    f.write(json.dumps({'type':'user','message':{'content':'Wait for me'}})+'\n')
                return nudge()
            delivery=FakeDelivery()
            code,out,err=sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live'],reviewer=FakeReviewer([changed]),delivery=delivery)
            self.assertEqual(code,0,err); self.assertEqual(delivery.calls,[])
            self.assertIn('stale',(sb.root/'state/sidecarwatch/log.jsonl').read_text())

    def test_loop_turn_cap_with_state_loss(self):
        with Sandbox() as sb:
            sb.arm()
            clock=FakeClock()
            p=codex_session(sb.codex,sb.project,[('Goal','Work completed.')],shape='B',at=clock.now())
            answers=iter(['All tests passed.','Verification succeeded.'])
            def respond(session,text):
                append_turn(p,text,next(answers),clock.now())
                state=sb.root/'state/sidecarwatch/state.json'
                state.unlink(missing_ok=True)
            delivery=FakeDelivery(respond)
            code,out,err=sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live','--overnight','--max-turns','2','--interval','2m','--max-passes','3'],clock=clock,reviewer=FakeReviewer([nudge(nudge_type='continue')]*3),delivery=delivery)
            self.assertEqual(code,10,err)
            self.assertEqual(len(delivery.calls),2)
            from sidecarwatch.policy import CONTINUE, CLOSING
            for number,(session,text) in enumerate(delivery.calls,1):
                self.assertIn(f'reply {number}/2 | written by an AI reviewer (claude)',text)
                self.assertEqual(text.splitlines()[1],CONTINUE)
                self.assertEqual(text.splitlines()[1],CONTINUE)
                self.assertTrue(text.endswith(CLOSING))
            self.assertIn('max-turns',(sb.root/'state/sidecarwatch/needs-you.md').read_text())
            (sb.root/'state/sidecarwatch/state.json').unlink()
            code,out,err=sb.cli(['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live','--max-turns','2'],clock=clock,reviewer=FakeReviewer([nudge()]),delivery=delivery)
            self.assertEqual(code,10); self.assertEqual(len(delivery.calls),2)

    def test_logs_hold_no_transcript(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('TRANSCRIPT_ONLY_USER','TRANSCRIPT_ONLY_AGENT')],shape='B')
            args=['watch','--only',str(sb.project)]
            sb.cli(args,reviewer=FakeReviewer([nudge()]))
            root=sb.root/'state/sidecarwatch'
            for path in root.rglob('*'):
                if path.is_file():
                    text=path.read_text()
                    for marker in ['TRANSCRIPT_ONLY_USER','TRANSCRIPT_ONLY_AGENT','TOOL_OUTPUT_ONLY']:
                        self.assertNotIn(marker,text)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_review_budget_and_bad_limits(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Goal','Done')])
            state=State(sb.root/'state/sidecarwatch')
            state.data['reviews']=[FakeClock().now().timestamp()-1]*20; state.save()
            fake=FakeReviewer([])
            code,out,err=sb.cli(['watch','--only',str(sb.project)],reviewer=fake)
            self.assertIn('review-budget',out); self.assertEqual(fake.calls,[])
            for opts in [['--live'],['--overnight'],['--interval','1s'],['--for','25h'],['--max-passes','0']]:
                self.assertEqual(sb.cli(['watch','--only',str(sb.project)]+opts)[0],2)

    def test_live_status_and_json(self):
        with Sandbox() as sb:
            sb.arm()
            p=codex_session(sb.codex,sb.project,[('Goal','Done')],shape='B')
            args=['watch','--only',str(sb.project),'--auto-reply','--allow',str(sb.project),'--live']
            delivery=FakeDelivery()
            code,out,err=sb.cli(args,reviewer=FakeReviewer([nudge()]),delivery=delivery)
            self.assertEqual(code,0,err)
            self.assertIn('Action: sent',out)
            self.assertNotIn('Nothing was sent.',out)
            append_turn(p,'Check another case','Tests passed.')
            code,out,err=sb.cli(args+['--json'],reviewer=FakeReviewer([nudge()]),delivery=delivery)
            self.assertEqual(code,10,err)
            self.assertEqual(json.loads(out)['results'][0]['reason'],'cooldown')

    def test_profile_allow_and_match_never_grants(self):
        with Sandbox() as sb:
            sb.arm()
            codex_session(sb.codex,sb.project,[('Goal','Done')],shape='B')
            folder=sb.root/'config/sidecarwatch/profiles'; folder.mkdir(parents=True)
            profile=folder/'books.toml'
            profile.write_text('name="books"\nmatch=["bookshelf"]\n[auto_reply]\nallow=true\n')
            args=['watch','--only','books','--auto-reply','--live']
            self.assertEqual(sb.cli(args)[0],2)
            profile.write_text('name="books"\npaths=["'+str(sb.project)+'"]\n[auto_reply]\nallow=true\n')
            delivery=FakeDelivery()
            self.assertEqual(sb.cli(args,reviewer=FakeReviewer([nudge()]),delivery=delivery)[0],0)
            self.assertEqual(len(delivery.calls),1)

    def test_log_none_and_since(self):
        with Sandbox() as sb:
            clock=FakeClock()
            codex_session(sb.codex,sb.project,[(text_parts('MESSAGE_PR', 'IVATE'),text_parts('FINAL_PR', 'IVATE'))],shape='B',at=clock.now())
            config=sb.root/'cfg.toml'; config.write_text('[log]\ntext="none"\n')
            fake=FakeReviewer([nudge(summary=text_parts('REVIEW_PR', 'IVATE'),reply_kind='needs_user',question_for_user=text_parts('QUESTION_PR', 'IVATE'))])
            args=['watch','--only',str(sb.project),'--config',str(config)]
            code,out,err=sb.cli(args+['--since',(clock.now()+__import__('datetime').timedelta(seconds=1)).isoformat()],reviewer=fake,clock=clock)
            self.assertEqual(code,0); self.assertEqual(fake.calls,[])
            code,out,err=sb.cli(args,reviewer=fake,clock=clock)
            self.assertEqual(code,10,err)
            for path in (sb.root/'state/sidecarwatch').rglob('*'):
                if path.is_file():
                    text=path.read_text()
                    for marker in [text_parts('MESSAGE_PR', 'IVATE'),text_parts('FINAL_PR', 'IVATE'),text_parts('REVIEW_PR', 'IVATE'),text_parts('QUESTION_PR', 'IVATE')]:
                        self.assertNotIn(marker,text)

    def test_deadline_and_run_review_budget(self):
        with Sandbox() as sb:
            clock=FakeClock()
            for number in (2,3):
                codex_session(sb.codex,sb.project,[('Goal','Done')],id=f'aaaaaaaa-aaaa-4aaa-8aaa-{number:012d}',at=clock.now())
            config=sb.root/'cfg.toml'; config.write_text('[reviewer]\nmax_per_run=1\n')
            fake=FakeReviewer([verdict()])
            code,out,err=sb.cli(['watch','--only',str(sb.project),'--config',str(config),'--interval','60s','--for','1m'],clock=clock,reviewer=fake)
            self.assertEqual(code,10,err)
            self.assertEqual(len(fake.calls),1)
            self.assertIn('review-budget',out)
