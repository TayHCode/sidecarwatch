import json, os
from pathlib import Path
import subprocess, tempfile, time
from . import PREFIX
from .scrub import scrub, bounded_json, MAX_TEXT, review_check
from .state import Stopped

from .templates import TEMPLATES, body as template_body
from .rubric import CHECK_NAMES, REVIEWER_PROMPT, schema

SCHEMA = schema()

class ReviewError(Exception):
    pass

class InvalidVerdict(ReviewError):
    pass

class Verdict(dict):
    reason = ''

def validate(value, cfg=None, profile=None):
    def check(item, shape, path='reply'):
        kind = shape['type']
        expected = {'object':dict,'array':list,'string':str,'integer':int,'boolean':bool}[kind]
        if type(item) is not expected or 'enum' in shape and item not in shape['enum']:
            raise InvalidVerdict('invalid verdict type or enum')
        if kind == 'object':
            if set(item) != set(shape['required']):
                raise InvalidVerdict('invalid verdict fields at ' + path + '; expected exactly: ' +
                                     ', '.join(shape['required']) + '; see docs/event-shapes.md#reviewer-reply-schema')
            for key,val in item.items(): check(val,shape['properties'][key], path + '.' + key)
        elif kind == 'array':
            for val in item: check(val,shape['items'])
    check(value,SCHEMA)
    result = Verdict(json.loads(json.dumps(value)))
    result.profile = profile
    if cfg:
        fields = ('summary','correction_reason','draft','question_for_user')
        review_check([result[k] for k in fields] + [result['checks'][k]['note'] for k in CHECK_NAMES],cfg)
        for key in fields: result[key] = scrub(result[key],cfg)
        for item in result['checks'].values(): item['note'] = scrub(item['note'],cfg)
    reason = ''
    if result['kind'] == '1' and (result['template'] == 'none' or result['needs_user_topics']):
        reason = 'inconsistent-verdict'
    elif result['kind'] == '1' and result['label'] in ('NEEDS_USER','UNSURE'):
        reason = 'needs-user'
    elif result['template'] == 'already_decided' and not template_body('already_decided',profile,result['decided_index']):
        reason = 'decision-not-quotable'
    elif result['template'] != 'already_decided' and result['decided_index'] != -1:
        reason = 'inconsistent-verdict'
    elif result['confidence'] == 'low': reason = 'low-confidence'
    elif result['question_for_user']: reason = 'worker-question'
    elif result['label'] in ('NEEDS_USER', 'BLOCKED', 'UNSURE') or result['needs_user_topics']:
        reason = 'reviewer-needs-human'
    if reason:
        result['kind'], result.reason = '2', reason
    return result

def child_env():
    return {**{k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY')}, PREFIX + 'ACTIVE': '1'}

def run(argv, data=None, *, cwd=None, timeout=10, state=None, discard=False):
    if state:
        state.check()
    child = None
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL if discard else subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwd, env=child_env())
        deadline = time.monotonic() + timeout
        while True:
            if state:
                state.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReviewError('CLI timed out')
            try:
                out, _ = child.communicate(data, timeout=min(2, remaining))
                return child.returncode, out or ''
            except subprocess.TimeoutExpired:
                data = None
    except OSError as exc:
        raise ReviewError('CLI unavailable; install and log in, or use --reviewer with the same CLI (not a cross-check)') from exc
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if child is not None:
            for pipe in (child.stdin, child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()

class Capabilities:
    def __init__(self, cfg):
        self.cfg, self.cache, self.status_cache = cfg, {}, {}
    def help(self, agent, sub=''):
        key = agent, sub
        if key not in self.cache:
            try:
                code, text = run([self.cfg['reviewer'][agent]['command']] + ([sub] if sub else []) + ['--help'])
                self.cache[key] = text if code == 0 else ''
            except ReviewError:
                self.cache[key] = ''
        return self.cache[key]
    def status(self, agent):
        """Probe help and a documented local login-status command, never a prompt."""
        if agent in self.status_cache:
            return self.status_cache[agent]
        top = self.help(agent)
        mode = top if agent == 'claude' else self.help(agent, 'exec')
        required = [('print mode', ('--print', '-p')), ('JSON output', ('--output-format',))] if agent == 'claude' else [
            ('exec mode', ('exec',)), ('read-only sandbox', ('--sandbox', '-s')),
            ('color control', ('--color',)), ('working directory', ('--cd', '-C')),
            ('answer file', ('--output-last-message', '-o'))]
        missing = [name for name, choices in required if not any(flag in (top if name == 'exec mode' else mode) for flag in choices)]
        login, reason = 'unverified', ''
        if not top:
            reason = 'CLI executable unavailable or --help failed; check the configured command (or use --reviewer with the same CLI)'
        elif missing:
            reason = 'missing non-interactive capability: ' + ', '.join(missing)
        else:
            sub = 'auth' if agent == 'claude' else 'login'
            if sub in top and 'status' in self.help(agent, sub):
                try:
                    code, output = run([self.cfg['reviewer'][agent]['command'], sub, 'status'] + (['--json'] if agent == 'claude' else []))
                    if code:
                        login = 'not-logged-in'
                    elif agent == 'codex':
                        login = 'logged-in'
                    else:
                        value = answer(output)
                        if type(value.get('loggedIn')) is bool:
                            login = 'logged-in' if value['loggedIn'] else 'not-logged-in'
                except ReviewError:
                    login = 'unverified'
            if login == 'not-logged-in':
                reason = 'CLI login status reports not logged in; log in locally'
        result = dict(capable=bool(top) and not missing, login=login, reason=reason,
                      ready=bool(top) and not missing and login != 'not-logged-in')
        self.status_cache[agent] = result
        return result

def answer(text):
    try:
        if len(text) > MAX_TEXT:
            raise ValueError()
        value = bounded_json(text)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise ReviewError('invalid reviewer answer; run doctor and check CLI version/capabilities and docs/event-shapes.md; share no raw transcript') from exc

class CLIReviewer:
    agent = ''
    def __init__(self, cfg, state, probe=None):
        self.cfg, self.state = cfg, state
        self.probe = probe or Capabilities(cfg)
    def check(self):
        if not self.cfg['privacy']['allow_cross_vendor_review']:
            raise ReviewError('cross-vendor review is OFF; explicit consent is required')
        status = self.probe.status(self.agent)
        if not status['ready']:
            raise ReviewError(status['reason'])
    def review(self, system_text, data_block):
        self.check()
        settings = self.cfg['reviewer'][self.agent]
        help_text = self.probe.help(self.agent)
        self.state.ensure()
        cwd = self.state.root / 'reviewer-cwd'
        cwd.mkdir(exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=self.state.root) as temp:
            argv = [settings['command']]
            if self.agent == 'claude':
                argv += ['-p', '--output-format', 'json']
                for flag, values in [('--no-session-persistence', []), ('--tools', ['']), ('--system-prompt', [system_text]), ('--json-schema', [json.dumps(SCHEMA)])]:
                    if flag in help_text:
                        argv += [flag] + values
                fallback = []
                if '--system-prompt' not in help_text:
                    fallback.append(system_text)
                if '--json-schema' not in help_text:
                    fallback.append(json.dumps(SCHEMA))
                data = '\n\n'.join(fallback + [data_block])
                if settings['model']:
                    argv += ['--model', settings['model']]
            else:
                help_text = self.probe.help('codex', 'exec')
                argv += ['exec', '-s', 'read-only', '--color', 'never', '-C', str(cwd)]
                schema_path, answer_path = Path(temp)/'schema.json', Path(temp)/'answer.json'
                schema_path.write_text(json.dumps(SCHEMA)); schema_path.chmod(0o600)
                for flag in ('--ephemeral', '--skip-git-repo-check'):
                    if flag in help_text:
                        argv += [flag]
                if '--output-schema' in help_text:
                    argv += ['--output-schema', str(schema_path)]
                argv += ['-o', str(answer_path)]
                data = system_text + '\n\n' + data_block
                if '--output-schema' not in help_text:
                    data = json.dumps(SCHEMA) + '\n\n' + data
                if settings['model']:
                    argv += ['-m', settings['model']]
            argv += settings['extra_args'] + (['-'] if self.agent == 'codex' else [])
            code, output = run(argv, data, cwd=cwd, timeout=self.cfg['reviewer']['timeout_seconds'], state=self.state)
            if code:
                raise ReviewError('reviewer failed; check CLI login')
            if self.agent == 'codex':
                try:
                    return answer(answer_path.read_text())
                except OSError as exc:
                    raise ReviewError('missing reviewer answer; run doctor and check CLI version/capabilities and local login') from exc
            result = answer(output)
            if result.get('is_error'):
                raise ReviewError('reviewer failed; check CLI login')
            return result['structured_output'] if isinstance(result.get('structured_output'), dict) else answer(result.get('result', ''))

class ClaudeReviewer(CLIReviewer):
    agent = 'claude'

class CodexReviewer(CLIReviewer):
    agent = 'codex'
