from pathlib import Path
import unittest
from sidecarwatch import profiles
from sidecarwatch.sessions import Session
from sidecarwatch.config import ConfigError
from tests.helpers import Sandbox


class ProfileTests(unittest.TestCase):
    def test_session_name_matches_review_scope_only(self):
        with Sandbox() as sb:
            s = Session('codex','id',Path('.'),name='Bookshelf worker',cwd=str(sb.root/'other'))
            p = {**profiles.DEFAULT,'name':'books','match':['bookshelf'],'auto_reply':{'allow':True}}
            self.assertTrue(profiles.applies(s,p))
            self.assertEqual(profiles.match(s,[p])['name'],'books')
            self.assertEqual(profiles.allowed_paths([p]),[])
            self.assertIn('worktrees',profiles.text(profiles.DEFAULT))

    def test_profile_matching(self):
        with Sandbox() as sb:
            s=Session('claude','id',Path('.'),cwd=str(sb.project),folders={str(sb.project)})
            broad={**profiles.DEFAULT,'name':'broad','paths':[str(sb.root)]}
            specific={**profiles.DEFAULT,'name':'specific','paths':[str(sb.project)]}
            self.assertEqual(profiles.match(s,[broad,specific])['name'],'specific')
            partial={**profiles.DEFAULT,'name':'partial','match':['BOOKSHELF']}
            self.assertEqual(profiles.match(s,[partial])['name'],'partial')
            self.assertEqual(profiles.match(s,[])['name'],'default')
            folder=sb.root/'config/sidecarwatch/profiles'; folder.mkdir(parents=True)
            (folder/'books.toml').write_text('name="books"\npaths=["'+str(sb.project)+'"]\n')
            self.assertEqual(sb.cli(['sessions','--only','books'])[0],0)
            (folder/'books.toml').write_text('name="books"\nunknown=true\n')
            with self.assertRaises(ConfigError):
                profiles.load(folder.parent)
