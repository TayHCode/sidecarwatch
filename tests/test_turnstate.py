from tests.helpers import text_parts
import json
import os
import unittest
from sidecarwatch.config import DEFAULTS, merge
SUCCESS = ('Done.', 'Tests passed.', 'Added CSV export.', 'The current file contains the requested change.')
from sidecarwatch.policy import Options, TEMPLATES, decide
from sidecarwatch.state import State
from sidecarwatch.codex_format import parse as dp
from sidecarwatch.claude_format import parse as cp
from sidecarwatch.reviewer import validate
from tests.helpers import Sandbox, FakeClock, FakeReviewer, FakeDelivery, codex_session, claude_session, nudge

AUDIT_FINALS = ['Should I proceed? It will delete the users table.', 'Should I continue and wipe the staging data?', text_parts('OK to proceed with removing the au', 'th checks?'), 'I can rebase or squash everything. Tell me which.', 'Reply yes and I will email all customers.', 'Delete the old output next.', 'Use --force next.', 'Overwrite the old results next.', 'Touch production next.', 'Handle payment next.', text_parts('Use cr', 'edentials next.'), 'Push the changes next.', 'Release the build next.', 'Test run ended in error.', 'Task failed.', 'A traceback was returned.', 'I am blocked.', 'Which option works?', 'Could I continue.', 'Confirm the next step.', 'Waiting for your approval.', 'Please clarify.', 'The next step costs $10.', 'Add an additional feature.', 'Would it be all right to proceed.', 'Is it OK if I proceed.', 'Are you happy for me to proceed.', 'Do I have your consent to continue.', 'I need a go ahead from you.', 'Your consent is required before proceeding.', 'Awaiting your response.', 'Any objections to continuing.', 'May the cleanup proceed.', 'What do you think.', 'Proceed with cleanup？', 'Continue with cleanup﹖', 'Proceed with cleanup‽', 'هل أتابع؟', '¿Puedo continuar.', 'Soll ich fortfahren.', '是否继续？', 'Продолжать？', 'Remove the backups next.', 'Run rm -rf ./backups next.', 'Truncate the customer table next.', 'Discard the uncommitted changes next.', 'Send the build to all customers next.', 'Transfer funds from the account next.', text_parts('Read the ke', 'y from the ke', 'ychain next.'), 'Run terraform apply next.', 'Erase everything next.', 'Forcefully replace the output next.', 'The step will de\u200blete the old data.', 'The step will dеlete the old data.', 'Next action: ｐｕｓｈ changes.', 'Next action: p\u200bush changes.', 'Next action: pro\u200bduction.', 'Next action: re\u200blease.', 'Borraré la tabla de usuarios.', 'Je vais supprimer les sauvegardes.', '删除所有备份。', 'Die Sicherungen werden gelöscht.', 'Process exited with code 1.', 'Command returned nonzero status 127.', 'fatal: unable to access the repository', 'Segmentation fault (core dumped)', 'EACCES: access denied', 'Permission denied.', 'Connection refused.', 'The test run did not succeed.', '2 tests failing.', 'Compilation unsuccessful.', 'The operation timed out.', 'Process terminated unexpectedly.', 'AssertionError: value mismatch', 'Traceback (most recent call last):', 'All good.\nERROR: tests incomplete', 'Build FAILED', 'Proceed with the cleanup?', 'Would you like me to finish.']
ADDITIONAL = [
    'Completed successfully.\n\nRemove the last backup before proceeding.',
    text_parts('All tests passed.\n\nYour assent will au', 'thorize the transfer.'),
    '> Completed successfully.\n> Continue only after approval.',
    '```text\nCompleted successfully.\nrm -rf ./archive\n```',
    'Completed successfully, assuming the remaining checks succeed.',
    'Probably done; a continuation would accept the invoice.',
    'Nothing more to do until you permit the migration.',
    text_parts('I could finish by replacing the cr', 'edentials if that seems sensible.'),
    'Ready for your nod before posting the package.',
    'Success on step one; the rollback did not complete.',
    'Tests passed locally; the remote run is still pending.',
    'All tests passed except the restore test.',
    'Terminé. Votre accord autorisera la suppression.',
    'Listo. Falta autorizar el pago.',
    '完了。続行するとバックアップを削除します。',
    'تم الاختبار. أحتاج موافقتك للحذف.',
    'Fertig — zum Fortfahren ist Ihre Freigabe erforderlich.',
    'Done.\n{"next_action":"discard local edits","consent":null}',
    'Done.\n- [x] build\n- [ ] accept recurring charges',
    text_parts('Done.\n<permission>reuse the stored access ke', 'y</permission>'),
]


class CompletionTests(unittest.TestCase):
    def test_audit_eighty_and_twenty_new_finals_all_templates(self):
        self.assertEqual((len(AUDIT_FINALS), len(ADDITIONAL)), (80, 20))
        with Sandbox() as sb:
            clock = FakeClock(); sb.arm(clock)
            state = State(sb.root/'state/sidecarwatch', clock)
            cfg = merge(DEFAULTS, {})
            for final in AUDIT_FINALS + ADDITIONAL:
                for make, parse, home in ((codex_session, dp, sb.codex), (claude_session, cp, sb.claude)):
                    path = make(home, sb.project, [('Goal', final)], at=clock.now())
                    session = parse(path, clock.now(), 60); session.folders = {str(sb.project)}
                    for template in TEMPLATES:
                        with self.subTest(case=final, agent=session.agent, template=template):
                            result = decide(session, validate(nudge(nudge_type=template,decided_index=0 if template=='already_decided' else -1),profile={'decided':['Use the standard csv module.']}), state, cfg,
                                            Options(True, [str(sb.project)], True, True), clock.now(),
                                            fresh=lambda: session, available=True)
                            self.assertNotEqual(result.reason, 'send')
                            self.assertTrue(result.needs_user)
            # The real orchestration must persist the pause, not just decline delivery.
            for final in ADDITIONAL:
                codex_session(sb.codex, sb.project, [('Goal', final)], at=clock.now())
                delivery = FakeDelivery()
                code, out, err = sb.cli(['watch', '--agent', 'codex', '--only', str(sb.project), '--allow', str(sb.project),
                                        '--auto-reply', '--live', '--overnight'], clock=clock,
                                       reviewer=FakeReviewer([nudge(nudge_type='continue')]), delivery=delivery)
                self.assertIn(code, (5, 8, 10), err)
                self.assertFalse(delivery.calls)
                self.assertIn('Needs you', (state.root/'needs-you.md').read_text())

    def test_text_cannot_replace_structured_completion(self):
        from sidecarwatch.turnstate import clean_completion
        with Sandbox() as sb:
            clock = FakeClock()
            for make, parse, home in ((codex_session, dp, sb.codex), (claude_session, cp, sb.claude)):
                for text in SUCCESS:
                    path = make(home, sb.project, [('Goal', text)], at=clock.now())
                    session = parse(path, clock.now(), 60)
                    self.assertTrue(clean_completion(session))
                    for altered in ('"'+text+'"', '```\n'+text+'\n```', text+'\nProceed', text+'\u200b', 'Ｄｏｎｅ.'):
                        session.final_message = altered
                        self.assertFalse(clean_completion(session))
                path = make(home, sb.project, [('Goal', 'Done.')], at=clock.now())
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if home == sb.codex:
                    rows = [r for r in rows if r.get('payload', {}).get('type') not in ('task_started', 'task_complete')]
                else:
                    rows[-1]['message'].pop('stop_reason')
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                os.utime(path, (clock.now().timestamp()-120,)*2)
                session = parse(path, clock.now(), 60)
                self.assertEqual(session.state, 'idle')
                self.assertFalse(clean_completion(session))

    def test_explicit_errors_survive_a_success_final(self):
        from sidecarwatch.turnstate import clean_completion
        with Sandbox() as sb:
            clock = FakeClock()
            for signal in ({'is_error': True}, {'exit_code': 1}, {'status': 'failed'},
                           {'output': 'Process exited with code 127.'}, {'output': 'Segmentation fault'}):
                for make, parse, home in ((codex_session, dp, sb.codex), (claude_session, cp, sb.claude)):
                    path = make(home, sb.project, [('Goal', 'Done.')], at=clock.now())
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    if home == sb.codex:
                        rows.insert(-2, {'type': 'response_item', 'payload': {'type': 'function_call_output', **signal}})
                    else:
                        rows.insert(-1, {'type': 'user', 'message': {'content': [{'type': 'tool_result', **signal}]}})
                    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    self.assertFalse(clean_completion(parse(path, clock.now(), 60)))
