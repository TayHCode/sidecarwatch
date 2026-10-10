import unittest
from tests import test_gates
from tests.helpers import append_turn, nudge
from sidecarwatch.claude_format import parse
from sidecarwatch.sessions import folders
from sidecarwatch.policy import label, TEMPLATES
from sidecarwatch.state import State


class LoopTests(unittest.TestCase):
    setUp = test_gates.GateTests.setUp
    choice = test_gates.GateTests.choice

    def test_loop_turn_cap_and_state_loss(self):
        self.opts.max_turns=2
        for i in range(2):
            text=label(self.s,TEMPLATES['back_to_goal'],'codex',2)
            append_turn(self.path,text,('Tests passed.', 'Verification succeeded.')[i],self.clock.now())
            self.s=parse(self.path,self.clock.now(),60); self.s.folders=folders(self.s.cwd,{})
        self.state.save(); (self.state.root/'state.json').unlink()
        self.state=State(self.state.root, self.clock)
        self.assertEqual(self.choice().reason,'max-turns')
        append_turn(self.path,'Now check edge cases','Tests passed.',self.clock.now())
        self.s=parse(self.path,self.clock.now(),60); self.s.folders=folders(self.s.cwd,{})
        self.assertEqual(self.s.auto_turns,0)
        self.assertEqual(self.choice().reason,'max-turns')

    def test_loop_repeat_and_no_progress(self):
        self.s.auto_recent=[TEMPLATES['back_to_goal']+'!']
        self.assertEqual(self.choice().reason,'repeat-reply')
        self.s.auto_recent=[]; self.s.auto_turns=1; self.s.previous_final=self.s.final_message+'!'
        self.assertEqual(self.choice().reason,'no-progress')
