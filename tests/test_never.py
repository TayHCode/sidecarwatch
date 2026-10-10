from tests.helpers import text_parts
import unittest
from sidecarwatch.policy import never_check
from sidecarwatch.config import merge, DEFAULTS, ConfigError


class NeverTests(unittest.TestCase):
    def test_never_reply_text(self):
        cases={
            'money':['Pay the bill','Buy it','$50','Upgrade your plan','Issue a refund','Use 20 USD'],
            text_parts('cr', 'edentials'):[text_parts('Send the pa', 'ssword'),text_parts('Rotate the ke', 'y'),'Read .env',text_parts('Use OAu', 'th'),text_parts('Paste your to', 'ken'),text_parts('Share cr', 'edentials')],
            'release':['Publish it','Deploy it','Ship it','Merge the branch into main','Change visibility','Tag v1.2'],
            'scope_change':['Also add a page','New feature','Expand the scope','Instead use another library','Move to a new system','Skip the tests'],
            'decision':['I approve','Permission granted','Use option A','Yes, proceed','Go ahead with it','Delete the database'],
        }
        clean_cases = {
            'money': ['Check the number formatting.', 'Summarize the current work.', 'Keep the example small.'],
            text_parts('cr', 'edentials'): ['Use generic sample data.', 'Describe the current test.', 'Re-read the stated goal.'],
            'release': ['Report the local results.', 'Stay within the current task.', 'Explain the observed behavior.'],
            'scope_change': ['Keep the implementation simple.', 'Compare results with the goal.', 'Summarize the evidence so far.'],
            'decision': ['Stop and wait for the user.', 'Describe what remains.', 'Check the current task tests.'],
        }
        for kind,texts in cases.items():
            for text in texts:
                with self.subTest(kind=kind,text=text):
                    self.assertIn(kind,never_check(text,'Completed export.',False))
            for clean in clean_cases[kind]:
                self.assertNotIn(kind,never_check(clean,'Completed export.',False))

    def test_never_worker_asks(self):
        for text,kind in [('Which approach should I use?','decision'),('Please confirm the next step.','decision'),('Should I buy it?','money'),(text_parts('May I see the pa', 'ssword?'),text_parts('cr', 'edentials')),('Ready to deploy?','release'),('Should I add a new feature?','scope_change')]:
            self.assertIn(kind,never_check('Stay on task.',text,True))
        self.assertEqual(never_check('Stay on task.','I updated the release notes.',False),['release'])
        self.assertEqual(never_check('Stay on task.','Should I continue?',False),['decision'])
        self.assertEqual(never_check('Stay on task.','Should I continue?',True),['decision'])
        self.assertEqual(never_check('Stay on task.','Which option should I choose?',True),['decision'])

    def test_never_cannot_be_disabled(self):
        for key in ['disable','patterns']:
            with self.assertRaises(ConfigError):
                merge(DEFAULTS,{'never':{key:[]}})
        self.assertEqual(never_check('touch database','Done.',False,extra_patterns=['database']),['custom'])
