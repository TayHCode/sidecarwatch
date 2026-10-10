from tests.helpers import text_parts
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def tracked():
    result = subprocess.run(['git','ls-files','-z'], cwd=ROOT, capture_output=True)
    if result.returncode == 0:
        return [Path(p) for p in result.stdout.decode().split('\0') if p]
    return [p.relative_to(ROOT) for p in ROOT.rglob('*') if p.is_file() and not any(part in ('.git','.scratch','__pycache__') for part in p.relative_to(ROOT).parts)]


def terms(path):
    if path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError('terms file must exist outside the repository')
    text = path.read_text(encoding='utf-8')
    if text.lstrip().startswith(('[','{')):
        value = json.loads(text)
        if isinstance(value,dict):
            if 'terms' in value:
                value = value['terms']
            else:
                categories = value.get('categories')
                if not isinstance(categories,dict):
                    raise ValueError('unsupported terms format')
                value = [term for group in categories.values() for term in (group.get('terms',[]) if isinstance(group,dict) else group)]
    else:
        value = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not isinstance(value,list) or not value or any(not isinstance(term,str) or not term.strip() for term in value):
        raise ValueError('empty or invalid terms list')
    return [term.lower() for term in value]


def scan(private_terms):
    hits = []
    for relative in tracked():
        path = ROOT / relative
        text = path.read_text(encoding='utf-8',errors='replace')
        for number, line in enumerate([str(relative)] + text.splitlines()):
            for position, term in enumerate(private_terms,1):
                if term in line.lower():
                    hits.append((str(relative),number,position))
    result = subprocess.run(['git','log','--format=%an%n%ae%n%cn%n%ce%n%B'], cwd=ROOT, capture_output=True, text=True)
    if result.returncode == 0:
        for number,line in enumerate(result.stdout.splitlines(),1):
            for position,term in enumerate(private_terms,1):
                if term in line.lower():
                    hits.append(('git-history',number,position))
    return hits


class ForbiddenTests(unittest.TestCase):
    def test_forbidden_terms(self):
        supplied = os.environ.get('FORBIDDEN_TERMS_FILE')
        if not supplied:
            self.skipTest(text_parts('set FORBIDDEN_TERMS_FILE to run the pr', 'ivate-terms check'))
        try:
            private_terms = terms(Path(supplied).expanduser())
            hits = scan(private_terms)
        except (OSError, ValueError, TypeError):
            self.fail(text_parts('pr', 'ivate-terms check could not read a valid external list'))
        # Never include terms, matched text, or private source content in output.
        self.assertEqual(len(hits), 0, 'count ' + str(len(hits)) + '\n' + '\n'.join(sorted({f'{p}:{n}' for p,n,_ in hits})))
