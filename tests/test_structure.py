from tests.helpers import text_parts
import ast
from pathlib import Path
import re
import tomllib
import unittest
from tests.test_forbidden_terms import ROOT, tracked


class StructureTests(unittest.TestCase):
    def test_imports_and_no_forbidden_machinery(self):
        allowed=set(text_parts('argparse dataclasses datetime difflib fcntl hashlib json os pathlib re se', 'crets shutil signal subprocess sys tempfile time tomllib typing unicodedata').split())
        forbidden=['shell'+'=True','os.'+'system','os.'+'popen','tm'+'ux','cron'+'tab','system'+'ctl','launch'+'ctl']
        for p in (ROOT/'sidecarwatch').glob('*.py'):
            source=p.read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node,ast.Import):
                    self.assertTrue(all(x.name.split('.')[0] in allowed for x in node.names),p.name)
                elif isinstance(node,ast.ImportFrom) and not node.level:
                    self.assertIn(node.module.split('.')[0],allowed,p.name)
            for item in forbidden:
                self.assertNotIn(item,source,p.name)
        config=tomllib.loads((ROOT/'pyproject.toml').read_text())
        self.assertEqual(config['project']['dependencies'],[])

    def test_repo_is_generic(self):
        patterns=[r'/(?:ho'+'me|Us'+r'ers)/[^/\s]+/',r'\b[A-Za-z0-9._%+-]+'+'@'+r'[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',r'\b(?:\d{1,3}\.){3}\d{1,3}\b',r'(?i)(?<!\w)[a-z]:\\',r'\b(?:s'+'k'+r'-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh'+'p'+r'_[A-Za-z0-9]{20,}|AK'+'IA'+r'[A-Z0-9]{16})\b']
        hits=[]
        for p in tracked():
            for number,line in enumerate([str(p)]+(ROOT/p).read_text(errors='replace').splitlines()):
                for index,pattern in enumerate(patterns):
                    for found in re.findall(pattern,line):
                        if index==1 and found.endswith('@'+'example.com') or index==2 and found=='127.'+'0.0.1':
                            continue
                        hits.append(f'{p}:{number}')
                if any(term in line.lower() for term in ('ren'+'der','sneako'+'scope')):
                    hits.append(f'{p}:{number}')
        self.assertFalse(hits,'count '+str(len(hits))+'\n'+'\n'.join(hits))

    def test_final_tree_has_no_executable_archive(self):
        for path in tracked():
            self.assertFalse(str(path).startswith(('original/','docs/original/')),str(path))
        self.assertFalse((ROOT/'CLAUDE.md').exists())
        forbidden={'sneako'+'scope','send','lanes','thread','start','watch','keepalive','s'+'sh'}
        for path in tracked():
            if path.suffix!='.py':
                continue
            for node in ast.walk(ast.parse((ROOT/path).read_text())):
                if not isinstance(node,ast.Call) or not node.args:
                    continue
                if not (isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name)
                        and node.func.value.id in ('subprocess','os')
                        and node.func.attr in ('run','Popen','system','popen','execv','execvp','posix_spawn')):
                    continue
                first=node.args[0]
                if not isinstance(first,(ast.List,ast.Tuple)) or not first.elts:
                    continue
                command=first.elts[0]
                if isinstance(command,ast.Constant) and isinstance(command.value,str):
                    self.assertNotIn(Path(command.value).name,forbidden,str(path))

    def test_subprocess_entrypoints(self):
        for p in (ROOT/'sidecarwatch').glob('*.py'):
            for node in ast.walk(ast.parse(p.read_text())):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='subprocess' and node.func.attr in ('run','Popen'):
                    self.assertIn(p.name,('sessions.py','reviewer.py'))
                    if p.name=='sessions.py':
                        self.assertEqual(ast.literal_eval(node.args[0].elts[0]),'git')
                    for keyword in node.keywords:
                        self.assertFalse(keyword.arg=='shell' and ast.literal_eval(keyword.value))
