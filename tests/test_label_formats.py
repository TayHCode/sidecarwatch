from tests.helpers import text_parts
import unittest
from sidecarwatch.scrub import scrub, ScrubUncertain
from tests.audit3_labels import extra, secret, tail
from tests.helpers import Sandbox, FakeReviewer, FakeDelivery, nudge, codex_session, claude_session

ADDITIONAL = [
    '**pásswörd**: '+secret+' '+tail,
    '### pásswörd\n'+secret+' '+tail,
    text_parts('- **Pa', 'SsWoRd** -> ')+secret+' '+tail,
    text_parts('| `PA', 'SSWORD` |\n| ')+secret+' '+tail+' |',
    'Ｐａｓｓ\u200bｗｏｒｄ\n'+secret+' '+tail,
    '**pass-word** ⇒ '+secret+' '+tail,
    '"pass word" = "'+secret+' '+tail+'"',
    '* pass\tword\n'+secret+' '+tail,
    '### Pаsswоrd\n\n'+secret+' '+tail,
    text_parts('pa', 'ssword /* ke', 'y explanation */ → ')+secret+' '+tail,
    text_parts('pa', 'ssword<br>\n')+secret+' '+tail,
    text_parts('pa', 'ssword\n-----\n')+secret+' '+tail,
    'pass<!--gap-->word : '+secret+' '+tail,
    'pass\u200dword\u2060 := '+secret+' '+tail,
]


class LabelFormatTests(unittest.TestCase):
    def test_punctuation_only_values_are_withheld(self):
        for text in (text_parts('Pa', 'ssword\n!!!'), text_parts(text_parts('pa', 'ssword'), ' = @#$%'), text_parts('**Pa', 'ssword**\n???')):
            with self.subTest(text=text), self.assertRaises(ScrubUncertain):
                scrub(text, strict=True)

    def test_audit_thirty_two_and_extra_formats_withhold_every_vendor_call(self):
        self.assertEqual(len(extra),32)
        for case,text,needle in extra+[(str(i),v,tail) for i,v in enumerate(ADDITIONAL)]:
            for agent,make in (('codex',codex_session),('claude',claude_session)):
                with self.subTest(case=case,agent=agent), Sandbox() as sb:
                    make(getattr(sb,agent),sb.project,[(text,'Done.')])
                    with self.assertRaises(ScrubUncertain): scrub(text,strict=True)
                    cfg=sb.root/'c.toml';cfg.write_text('[log]\ntext="digest"\n')
                    r=FakeReviewer([]);d=FakeDelivery()
                    code,out,err=sb.cli(['watch','--only',str(sb.project),'--config',str(cfg)],reviewer=r,delivery=d)
                    other=FakeReviewer([])
                    acode,aout,aerr=sb.cli(['audit',agent],reviewer=other)
                    self.assertEqual((code,acode),(8,8),(out,err,aout,aerr))
                    self.assertFalse(r.calls or other.calls or d.calls)
                    self.assertFalse((sb.fake/'calls.jsonl').exists())
                    shown=sb.cli(['show',agent,'--chars','100000'])[1]
                    stored=''.join(p.read_text() for p in (sb.root/'state/sidecarwatch').glob('*') if p.is_file())
                    for surface in (scrub(text),out,err,aout,aerr,shown,stored):
                        self.assertNotIn(needle,surface)

    def test_label_and_value_in_separate_messages_or_outside_window(self):
        for agent,make in (('codex',codex_session),('claude',claude_session)):
            with self.subTest(agent=agent), Sandbox() as sb:
                make(getattr(sb,agent),sb.project,[(text_parts('Pa', 'ssword'),secret+' '+tail)])
                r=FakeReviewer([])
                code,out,err=sb.cli(['audit',agent],reviewer=r)
                self.assertEqual(code,8)
                self.assertFalse(r.calls)
                self.assertNotIn(tail,out+err)
                for args in (['show',agent],['show',agent,'--json']):
                    self.assertNotIn(tail,sb.cli(args)[1])

    def test_reviewer_label_split_between_fields_never_reaches_output_or_state(self):
        with Sandbox() as sb:
            codex_session(sb.codex,sb.project,[('Goal','Done.')])
            r=FakeReviewer([nudge(summary=text_parts('Pa', 'ssword'),question_for_user=secret+' '+tail)])
            code,out,err=sb.cli(['watch','--only',str(sb.project)],reviewer=r)
            self.assertEqual(code,8)
            stored=''.join(p.read_text() for p in (sb.root/'state/sidecarwatch').glob('*') if p.is_file())
            self.assertNotIn(tail,out+err+stored)
