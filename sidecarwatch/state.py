import datetime as dt
import fcntl, json, os
from pathlib import Path
import time
from . import PREFIX
from .ledger import SendLog
from .storage import write_private

class Clock:
    def now(self):
        return dt.datetime.now(dt.timezone.utc)
    def monotonic(self):
        return time.monotonic()
    def sleep(self, seconds):
        time.sleep(seconds)

class Busy(Exception):
    pass

class Stopped(Exception):
    pass

class State:
    def __init__(self, root, clock=None):
        self.clock = clock or Clock()
        self.root = Path(root)
        self.send_log = SendLog(self)
        self.data = {'sessions': {}, 'folders': {}, 'reviews': [], 'pauses': {}, 'needs': {}}
        try:
            value = json.loads((self.root / 'state.json').read_text())
            if isinstance(value, dict) and isinstance(value.get('sessions'), dict) and isinstance(value.get('folders'), dict) and isinstance(value.get('reviews'), list):
                self.data = value
        except (OSError, ValueError, RecursionError):
            pass
        pauses = self.data.get('pauses', {})
        self.data['pauses'] = {k: v for k, v in pauses.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(pauses, dict) else {}
        if not isinstance(self.data.get('needs'),dict):
            self.data['needs'] = {}
    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
    def write(self, name, text):
        write_private(self.root / name, text)
    def save(self):
        self.write('state.json', json.dumps(self.data))
    def stopped(self):
        return os.environ.get(PREFIX + 'STOP', '').strip().lower() not in ('', '0', 'false', 'no', 'off') or (self.root / 'STOP').exists()
    def unseen_notes(self, notes):
        """Deduplicate diagnostics across commands; retain only fingerprints."""
        import hashlib
        path = self.root / 'parse-notices.json'
        try:
            stored = json.loads(path.read_text())
            seen = set(stored) if isinstance(stored, list) and all(isinstance(x, str) for x in stored) else set()
        except (OSError, ValueError):
            seen = set()
        fresh = []
        for note in notes:
            filename = note.rsplit(': line ', 1)[0]
            try:
                info = Path(filename).stat()
                revision = str(info.st_size) + ':' + str(info.st_mtime_ns)
            except OSError:
                revision = ''
            key = hashlib.sha256((note + ':' + revision).encode()).hexdigest()
            if key not in seen:
                fresh.append(note)
                seen.add(key)
        if fresh:
            self.write(path.name, json.dumps(sorted(seen)[-1000:]))
        return fresh
    def check(self):
        if self.stopped():
            raise Stopped('stopped')
    def acquire(self):
        self.ensure()
        f = (self.root / 'watch.lock').open('a')
        os.chmod(f.name, 0o600)
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            f.close()
            raise Busy('another watch holds the lock') from exc
        return f
    def session(self, session):
        key = session.agent + ':' + session.id
        value = self.data['sessions'].get(key)
        if not isinstance(value, dict) or not isinstance(value.get('handled'), list) or not isinstance(value.get('sends'), list):
            value = {'handled': [], 'sends': []}
            self.data['sessions'][key] = value
        return value
    def handled(self, session):
        row = self.session(session)
        refusal = row.get('privacy_refusal', {})
        if refusal and refusal.get('evidence_key') != session.evidence_key:
            return False
        return session.turn_key in row['handled']
    def record(self, session, now, sent=False):
        row = self.session(session)
        row['handled'] = (row['handled'] + [session.turn_key])[-50:]
        if sent:
            row['last_sent_turn'] = session.turn_key
            row['sends'] = recent(row['sends'], now, 3600) + [now.timestamp()]
            for folder in session.folders:
                self.data['folders'][folder] = recent(self.data['folders'].get(folder, []), now, 86400) + [now.timestamp()]
        self.save()
    def log(self, record, cfg):
        self.ensure()
        path = self.root / 'log.jsonl'
        if path.exists() and path.stat().st_size > cfg['max_bytes']:
            os.replace(path, self.root / 'log.jsonl.1')
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
    def begin_needs(self):
        if (self.root / 'needs-you.md').exists():
            os.replace(self.root / 'needs-you.md', self.root / 'needs-you.prev.md')
        self.sync_needs()
    def append(self, name, text):
        self.ensure()
        fd = os.open(self.root/name,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        with os.fdopen(fd,'a',encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    def sync_needs(self):
        items=self.data['needs']
        lines=[f'# Needs you — {self.open_count()} open']
        for key,item in sorted(items.items(),key=lambda kv:kv[1]['since']):
            age=max(0,int((self.clock.now().timestamp()-item['since'])//60))
            detail = (f"- Question: {item['question']}\n" if item['question'] else '') + (f"- Draft answer: {item['draft']}\n" if item['draft'] else '')
            lines.append(f"## {item['name']} · {item['profile']} · waiting {age}m\n"
                         f"{self.data['pauses'].get(key,'')}\n"
                         f"{detail}- id: {item['id']}")
        lines += [text for key,text in self.data['pauses'].items() if key not in items]
        self.write('needs-you.md','\n\n'.join(lines)+'\n')
    def open_count(self):
        return len(self.data['pauses'])
    def need(self, text, session, verdict=None, profile=None, cfg=None):
        from .scrub import scrub
        key=session.agent+':'+session.id
        before=self.data['needs'].get(key,{})
        v=verdict or {}
        keep=not cfg or cfg['log']['text'] != 'none'
        clean=lambda value:scrub(value,(cfg or {}).get('scrub'))
        item={'id':session.id,'agent':session.agent,'name':clean(session.name or session.id),
              'profile':clean((profile or {}).get('name','default')),
              'question':clean(v.get('question_for_user', '')) if keep else '',
              'draft':clean(v.get('draft','')) if keep else '',
              'since':before.get('since',self.clock.now().timestamp())}
        self.data['needs'][key]=item
        if self.data['pauses'].get(key)!=text or before!=item:
            self.append('needs-you.log',json.dumps({'time':self.clock.now().isoformat(),'action':'upsert',**item})+'\n')
        self.data['pauses'][key]=clean(text)
        self.save(); self.sync_needs()
    def clear_need(self, session):
        key=session.agent+':'+session.id
        if self.data['pauses'].pop(key,None) is not None:
            self.data['needs'].pop(key,None)
            self.append('needs-you.log',json.dumps({'time':self.clock.now().isoformat(),'action':'resolved','session':key})+'\n')
            self.save(); self.sync_needs()
    def resolve(self, target):
        from .sessions import Session
        entries=list(self.data['needs'].values())
        found=[it for it in entries if it['id']==target or it['name']==target]
        if not found and len(target)>=6:
            found=[it for it in entries if it['id'].startswith(target)]
        if not found:
            raise LookupError('open item not found; run open and use an exact name/ID or a prefix of at least six characters')
        if len(found)!=1:
            raise LookupError('ambiguous open item: ' + ', '.join(it['id'] for it in found) + '; use an exact ID')
        item=found[0]
        self.clear_need(Session(item['agent'],item['id'],Path('.')))
        return item['name']

def recent(values, now, seconds):
    return [v for v in values if isinstance(v, (int, float)) and now.timestamp() - seconds < v <= now.timestamp()]
