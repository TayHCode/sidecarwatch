from tests.helpers import text_parts
import json
import os
import unittest
from unittest.mock import patch
from sidecarwatch.config import merge, DEFAULTS, FORBIDDEN_ARGS
from sidecarwatch.reviewer import ClaudeReviewer, CodexReviewer, REVIEWER_PROMPT, InvalidVerdict, ReviewError, validate, answer
from sidecarwatch.state import State
from tests.helpers import Sandbox, verdict, nudge, codex_session, FakeReviewer, FakeDelivery


class ReviewerTests(unittest.TestCase):
    def test_verdict_validation(self):
        self.assertEqual(validate(verdict())['label'],'WORKING')
        bad=[{k:v for k,v in verdict().items() if k!='summary'}, verdict(extra='bad'),verdict(status='invalid'),verdict(summary=12),verdict(on_task={'rating':'ok','note':'','extra':1})]
        for obj in bad:
            with self.subTest(obj=obj), self.assertRaises(InvalidVerdict):
                validate(obj)

    def test_verdict_normalisation(self):
        for value,reason in [(nudge(nudge_type='none'),'inconsistent-verdict'),
                             (nudge(needs_user_topics=['decision']),'inconsistent-verdict'),
                             (nudge(confidence='low'),'low-confidence'),
                             (nudge(label='UNSURE'),'needs-user')]:
            result=validate(value)
            self.assertEqual(result['kind'],'2')
            self.assertEqual(result.reason,reason)
        for value in (nudge(reply='Yes.'),nudge(reply='x'*601)):
            with self.assertRaises(InvalidVerdict): validate(value)

    def test_reviewer_argv(self):
        with Sandbox() as sb:
            (sb.fake/'verdict.json').write_text(json.dumps(verdict()))
            state=State(sb.root/'own'); cfg=merge(DEFAULTS,{'privacy': {'allow_cross_vendor_review': True}})
            with patch.dict(os.environ,{k:'fake-value' for k in (text_parts('ANTHROPIC_API_KE', 'Y'),text_parts('ANTHROPIC_AU', 'TH_TO', 'KEN'),text_parts('OPENAI_API_KE', 'Y'),text_parts('CODEX_API_KE', 'Y'))}):
                self.assertEqual(ClaudeReviewer(cfg,state).review(REVIEWER_PROMPT,'DATA'),verdict())
                self.assertEqual(CodexReviewer(cfg,state).review(REVIEWER_PROMPT,'DATA'),verdict())
            calls=[json.loads(line) for line in (sb.fake/'calls.jsonl').read_text().splitlines()]
            c=next(x for x in calls if x['argv'][0]=='-p')
            for flag in ['--tools','--no-session-persistence','--json-schema','--system-prompt']:
                self.assertIn(flag,c['argv'])
            self.assertEqual(c['argv'][c['argv'].index('--tools')+1],'')
            self.assertEqual(c['stdin'],'DATA')
            self.assertFalse(c[text_parts('had_api_ke', 'y')]); self.assertEqual(c['active'],'1')
            self.assertEqual(c['cwd'],str(state.root/'reviewer-cwd'))
            d=next(x for x in calls if x['argv'][0]=='exec' and '--help' not in x['argv'])
            for flag in ['--ephemeral','-s','read-only','--output-schema','-o']:
                self.assertIn(flag,d['argv'])
            self.assertEqual(d['argv'][-1],'-')
            self.assertFalse(d[text_parts('had_api_ke', 'y')])
            for call in calls:
                for flag in FORBIDDEN_ARGS:
                    self.assertNotIn(flag, call['argv'])
                self.assertFalse(call[text_parts('had_api_ke', 'y')])
                self.assertEqual(call['active'], '1')
            self.assertEqual(d['cwd'], str(state.root/'reviewer-cwd'))

    def test_reviewer_old_help_fallback(self):
        with Sandbox() as sb:
            (sb.fake/'verdict.json').write_text(json.dumps(verdict()))
            (sb.fake/'old-help').touch(); (sb.fake/'fallback').touch()
            ClaudeReviewer(merge(DEFAULTS,{'privacy': {'allow_cross_vendor_review': True}}),State(sb.root/'own')).review(REVIEWER_PROMPT,'DATA')
            call=json.loads((sb.fake/'calls.jsonl').read_text().splitlines()[-1])
            self.assertNotIn('--tools',call['argv'])
            self.assertIn(REVIEWER_PROMPT,call['stdin'])
            self.assertIn('additionalProperties',call['stdin'])
            CodexReviewer(merge(DEFAULTS,{'privacy': {'allow_cross_vendor_review': True}}),State(sb.root/'own')).review(REVIEWER_PROMPT,'DATA')
            call=json.loads((sb.fake/'calls.jsonl').read_text().splitlines()[-1])
            for flag in ['--ephemeral','--output-schema','--skip-git-repo-check']:
                self.assertNotIn(flag,call['argv'])
            self.assertIn(REVIEWER_PROMPT,call['stdin'])
            self.assertIn('additionalProperties',call['stdin'])

    def test_reviewer_failures(self):
        with Sandbox() as sb:
            (sb.fake/'verdict.json').write_text(json.dumps(verdict()))
            cfg=merge(DEFAULTS,{'privacy': {'allow_cross_vendor_review': True}, 'reviewer':{'timeout_seconds':1}})
            for marker,value in [('exit-code','3'),('is-error',''),('bad-output',''),('sleep','10')]:
                with self.subTest(marker=marker):
                    (sb.fake/marker).write_text(value)
                    with self.assertRaises(ReviewError):
                        ClaudeReviewer(cfg,State(sb.root/'own')).review(REVIEWER_PROMPT,'DATA')
                    (sb.fake/marker).unlink()
            cfg['reviewer']['claude']['command']=str(sb.root/'missing')
            with self.assertRaisesRegex(ReviewError,'same CLI'):
                ClaudeReviewer(cfg,State(sb.root/'own')).review(REVIEWER_PROMPT,'DATA')

    def test_nonzero_reviewer_reports_exit_and_stderr_without_login_guess(self):
        for service in (ClaudeReviewer, CodexReviewer):
            with self.subTest(service=service.agent), Sandbox() as sb:
                (sb.fake/'exit-code').write_text('3')
                (sb.fake/'stderr').write_text('Model unavailable\nTry a different model.')
                cfg = merge(DEFAULTS, {'privacy': {'allow_cross_vendor_review': True}})
                reviewer = service(cfg, State(sb.root/'own'))
                self.assertEqual(reviewer.probe.status(service.agent)['login'], 'logged-in')
                with self.assertRaises(ReviewError) as caught:
                    reviewer.review(REVIEWER_PROMPT, 'DATA')
                message = str(caught.exception)
                self.assertIn('exit status 3', message)
                self.assertIn('stderr: Model unavailable Try a different model.', message)
                self.assertNotIn('login', message)
                self.assertNotIn('log in', message)

    def test_nonzero_reviewer_login_advice_requires_authentication_evidence(self):
        for service in (ClaudeReviewer, CodexReviewer):
            with self.subTest(service=service.agent), Sandbox() as sb:
                (sb.fake/'exit-code').write_text('7')
                (sb.fake/'stderr').write_text('Authentication required: please log in.')
                cfg = merge(DEFAULTS, {'privacy': {'allow_cross_vendor_review': True}})
                with self.assertRaises(ReviewError) as caught:
                    service(cfg, State(sb.root/'own')).review(REVIEWER_PROMPT, 'DATA')
                self.assertIn('exit status 7', str(caught.exception))
                self.assertIn('check CLI login', str(caught.exception))

    def test_nonzero_reviewer_scrubs_before_shortening_stderr(self):
        for service in (ClaudeReviewer, CodexReviewer):
            with self.subTest(service=service.agent), Sandbox() as sb:
                private = text_parts('gh', 'p_', 'B'*36)
                (sb.fake/'exit-code').write_text('9')
                (sb.fake/'stderr').write_text('Model unavailable\n' + private + '\nLOCAL_MARKER ' + 'x '*40000)
                cfg = merge(DEFAULTS, {'privacy': {'allow_cross_vendor_review': True},
                                       'scrub': {'extra_terms': ['LOCAL_MARKER']}})
                with self.assertRaises(ReviewError) as caught:
                    service(cfg, State(sb.root/'own')).review(REVIEWER_PROMPT, 'DATA')
                message = str(caught.exception)
                self.assertIn('exit status 9', message)
                self.assertNotIn(private, message)
                self.assertNotIn('LOCAL_MARKER', message)
                self.assertIn('[REDACTED:', message)
                self.assertLess(len(message), 600)
                self.assertNotIn('login', message)

    def test_nonzero_reviewer_without_stderr_reports_that_fact(self):
        with Sandbox() as sb:
            (sb.fake/'exit-code').write_text('4')
            cfg = merge(DEFAULTS, {'privacy': {'allow_cross_vendor_review': True}})
            with self.assertRaises(ReviewError) as caught:
                ClaudeReviewer(cfg, State(sb.root/'own')).review(REVIEWER_PROMPT, 'DATA')
            self.assertIn('exit status 4', str(caught.exception))
            self.assertIn('no stderr', str(caught.exception))
            self.assertNotIn('login', str(caught.exception))

    def test_duplicate_keys_rejected_at_every_depth_and_envelope(self):
        for key, first, last in [('reply', 'YES', ''), ('nudge_type', 'release', 'continue'),
                                 ('needs_user_topics', ['money'], []), ('reply_kind', 'needs_user', 'nudge'),
                                 ('status', 'blocked', 'working'), ('confidence', 'low', 'high')]:
            value = nudge(nudge_type='continue'); value.pop(key,None)
            raw = json.dumps(value)[:-1] + ',' + json.dumps(key) + ':' + json.dumps(first) + ',' + json.dumps(key) + ':' + json.dumps(last) + '}'
            with Sandbox() as sb:
                sb.arm()
                codex_session(sb.codex, sb.project, [('Goal', 'Completed successfully.')])
                delivery = FakeDelivery()
                code, out, err = sb.cli(['watch', '--only', str(sb.project), '--allow', str(sb.project),
                                        '--auto-reply', '--live', '--overnight'],
                                       reviewer=FakeReviewer([lambda: answer(raw)]), delivery=delivery)
                self.assertEqual(code, 5, err)
                self.assertFalse(delivery.calls)
                self.assertIn('Needs you', out)
            for document in (raw, '{"structured_output":' + raw + '}',
                             '{"result":' + json.dumps(raw) + '}'):
                with self.subTest(key=key), self.assertRaises(ReviewError):
                    result = answer(document)
                    if 'result' in result:
                        answer(result['result'])
        for raw in ('{"is_error":true,"is_error":false}',
                    '{"structured_output":{},"structured_output":{}}',
                    '{"on_task":{"rating":"concern","rating":"ok"}}',
                    '{"status":"blocked","sta\\u0074us":"working"}'):
            with self.assertRaises(ReviewError):
                answer(raw)
        self.assertEqual(answer(json.dumps(nudge())), nudge())
