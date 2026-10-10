from pathlib import Path
from . import config, digest, policy, profiles, report, sessions, observations
from .reviewer import Capabilities, ClaudeReviewer, CodexReviewer, REVIEWER_PROMPT, ReviewError, run, validate

class Delivery:
    def __init__(self, cfg, state, probe=None):
        self.cfg, self.state = cfg, state
        self.probe = probe or Capabilities(cfg)
    def available(self, session):
        if session.agent == 'codex':
            help_text = self.probe.help('codex', 'queue')
            return self.cfg['delivery']['codex_queue'] and all(flag in help_text for flag in ('--thread', '--message'))
        help_text = self.probe.help('claude')
        return self.cfg['delivery']['claude_resume'] and '--resume' in help_text and '--print' in help_text and bool(session.cwd) and Path(session.cwd).is_dir()
    def send(self, session, text):
        enabled = self.cfg['delivery']['codex_queue' if session.agent == 'codex' else 'claude_resume']
        if not enabled or not policy.clean_completion(session):
            return False
        if session.state != 'idle' or not policy.valid_session_id(session.id) or len(text) > 1000 or not text.isascii() or not policy.valid_message(text):
            return False
        self.state.check()
        command = self.cfg['reviewer'][session.agent]['command']
        if session.agent == 'codex':
            argv, data, cwd, timeout = [command, 'queue', '--thread', session.id, '--message', text], None, self.state.root / 'reviewer-cwd', 90
            cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            argv = [command, '-p', '--resume', session.id] + self.cfg['delivery']['claude']['extra_args']
            data, cwd, timeout = text, session.cwd, self.cfg['delivery']['turn_timeout_seconds']
        try:
            return run(argv, data, cwd=cwd, timeout=timeout, state=self.state, discard=True)[0] == 0
        except ReviewError:
            return False
