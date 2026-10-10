import datetime as dt
import json
from pathlib import Path
import re
from . import config, digest, policy, profiles, report, sessions, observations, decide
from .reviewer import Capabilities, ClaudeReviewer, CodexReviewer, REVIEWER_PROMPT, ReviewError, run, validate
from .scrub import scrub, ScrubUncertain
from .state import recent
from .ledger import LedgerError, session_key

from .deliver import Delivery

def settings(args, cfg, profs, now):
    args.only = args.only or cfg['watch']['only']
    if not args.only and not args.session:
        raise config.ConfigError('watch requires --only or --session')
    if not args.auto_reply and (args.live or args.overnight or args.allow or args.max_turns is not None):
        raise config.ConfigError('--live, --overnight, --allow and --max-turns require --auto-reply')
    maximum = args.max_turns if args.max_turns is not None else cfg['auto_reply']['max_turns']
    if not 1 <= maximum <= 50:
        raise config.ConfigError('--max-turns: outside permitted limits')
    allow = args.allow + profiles.allowed_paths(profs)
    if args.auto_reply and not allow:
        raise config.ConfigError('--auto-reply requires an allow-list')
    if any(not p.strip() or not Path(p).expanduser().is_dir() for p in allow):
        raise config.ConfigError('--allow requires a nonempty existing folder')
    allow = [str(Path(p).expanduser().resolve()) for p in allow]
    interval = config.duration(args.interval) if args.interval else None
    if interval is not None and interval < 60:
        raise config.ConfigError('--interval must be at least 60s')
    if args.max_passes is not None and args.max_passes < 1:
        raise config.ConfigError('--max-passes must be positive')
    deadline = None
    if args.until:
        if not re.fullmatch(r'\d{2}:\d{2}', args.until):
            raise config.ConfigError('--until requires HH:MM')
        deadline = config.when(args.until, now, True)
    elif args.for_time or interval and args.auto_reply and args.live:
        duration = config.duration(args.for_time or '8h')
        if not 0 < duration <= 86400:
            raise config.ConfigError('--for must be positive and at most 24h')
        deadline = now + dt.timedelta(seconds=duration)
    return policy.Options(args.auto_reply, allow, args.overnight, args.live, maximum, deadline), interval

def watch(args, cfg, profs, homes, state, clock, out, err, reviewer=None, delivery=None):
    now = clock.now()
    options, interval = settings(args, cfg, profs, now)
    initial = not state.data.get('watch_initialized',False)
    cutoff = config.when(args.since, now) if args.since else now-dt.timedelta(minutes=30) if initial else dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    probe = Capabilities(cfg)
    delivery = delivery or Delivery(cfg, state, probe)
    lock, passes, review_count, failed = None, 0, 0, False
    refused, consent_needed = False, False
    try:
        state.check()
        if not args.list_only:
            lock = state.acquire()
            if not args.dry_run:
                state.begin_needs()
        while True:
            state.check()
            now = clock.now()
            ending = bool(options.deadline and now >= options.deadline)
            notes = []
            all_items = sessions.discover(homes, now, cfg['discovery'], state.root, notes)
            for note in state.unseen_notes(notes):
                print(note, file=err)
            options.transcript_day_count = sum(len(recent(s.auto_stamps, now, 86400)) for s in all_items)
            items = sessions.scope(all_items, args.only, profs)
            if args.session:
                selected = {(s.agent, s.id) for s in (sessions.target(all_items, value) for value in args.session)}
                items = [s for s in items if (s.agent, s.id) in selected]
            if args.agent:
                items = [s for s in items if s.agent == args.agent]
            due = sorted((s for s in items if s.state == 'idle' and s.last_finished_at and s.last_finished_at >= cutoff and not state.handled(s)), key=lambda s: s.last_finished_at)
            if args.list_only:
                print(report.listing([s for s in items if s in due or s.parse_limited or s.evidence and s.evidence.reason], profs, cfg['scrub'], now, args.json), file=out)
                return 0
            if args.init:
                for s in items:
                    if s.last_finished_at: state.record(s,now)
                state.data['watch_initialized']=True; state.save()
                print('Initialized: current finished turns marked seen.',file=out)
                return 0
            if initial and not args.since:
                for s in items:
                    if s.last_finished_at and s.last_finished_at < cutoff: state.record(s,now)
            state.data['watch_initialized']=True; state.save()
            initial=False
            counts = {'in_scope': len(items), 'reviewed': 0, 'sent': 0, 'would_send': 0, 'need_you': 0, 'previewed': 0}
            records, withheld = [], set()
            for session in items:
                reason = observations.reason(session, state, ending)
                if reason:
                    if reason.startswith('scrub-uncertain:'):
                        refused = True
                    withheld.add((session.agent, session.id))
                    record = (dict(session=session.id, action='refused' if reason.startswith('scrub-uncertain:') else 'local-preview', reason=reason)
                              if args.dry_run else observations.record(session, reason, state, cfg, now))
                    records.append(record or dict(session=session.id, action='refused' if reason.startswith('scrub-uncertain:') else 'flagged', reason=reason, repeated=True))
                    if not args.json and (record or reason.startswith('scrub-uncertain:')):
                        print(session.agent + ' ' + session.id[:8] + ': ' + report.explain(reason) + '\nAutomatic reply: NOT SENT', file=out)
            if ending:
                counts['need_you'] = state.open_count()
                if args.json:
                    print(json.dumps({**counts, 'results': records, 'stopped': 'deadline'}), file=out)
                else:
                    print('Stopped: deadline reached.', file=out)
                break
            for session in due:
                if (session.agent, session.id) in withheld:
                    continue
                state.check()
                now = clock.now()
                if options.deadline and now >= options.deadline:
                    break
                calls = recent(state.data['reviews'], now, 3600)
                if not args.dry_run and cfg['privacy']['allow_cross_vendor_review'] and (len(calls) >= cfg['reviewer']['max_per_hour'] or review_count >= cfg['reviewer']['max_per_run']):
                    if not args.json:
                        print('Could not review: ' + session.id[:8] + ' review-budget', file=out)
                    state.need(f'Needs you: {session.agent} {session.id[:8]}\nAutomatic replies paused for this session: review-budget', session)
                    state.record(session, now)
                    record = dict(time=now.isoformat(), agent=session.agent, session=session.id[:8], turn_key=(session.turn_key), action='flagged', reason='review-budget')
                    state.log(record, cfg['log'])
                    records.append(record)
                    continue
                who = ('claude' if session.agent == 'codex' else 'codex') if args.reviewer == 'auto' else args.reviewer
                block = ''
                result, body, reason, action, need = None, '', '', 'reviewed', False
                try:
                    profile = profiles.match(session, profs)
                    block = digest.build(session, profile, cfg, since=cutoff, overnight=args.auto_reply and args.overnight)
                    state.session(session).pop('privacy_refusal', None)
                    if args.dry_run:
                        record = dict(session=session.id, reviewer=who, action='local-preview', reason='dry-run', digest=block)
                        if not args.json:
                            print(f'Local dry run: WOULD send this scrubbed digest to {who}; nothing was sent.\n' + block, file=out)
                        records.append(record)
                        counts['previewed'] += 1
                        state.record(session, now)
                        continue
                    if not cfg['privacy']['allow_cross_vendor_review']:
                        print(report.review_notice(who, cfg), file=err)
                        consent_needed = True
                        continue
                    service = reviewer or (ClaudeReviewer if who == 'claude' else CodexReviewer)(cfg, state, probe)
                    if reviewer is None:
                        service.check()
                    print(f'Sending a scrubbed digest of {session.agent} session {session.id[:8]} to {who} for review.', file=err)
                    state.data['reviews'] = calls + [now.timestamp()]
                    state.save()
                    review_count += 1
                    result = decide.context(validate(service.review(REVIEWER_PROMPT, block), cfg['scrub'], profile), session, cfg['scrub'])
                    counts['reviewed'] += 1
                    def fresh():
                        if result['template']=='already_decided' and not profiles.unchanged(profile):
                            return None
                        from .claude_format import parse as cp
                        from .codex_format import parse as dp
                        try:
                            current = (cp if session.agent == 'claude' else dp)(session.path, clock.now(), cfg['discovery']['quiet_seconds'])
                            current.folders = sessions.folders(current.cwd, {})
                            return current
                        except OSError:
                            return None
                    decision = policy.decide(session, result, state, cfg, options, clock.now(), fresh=fresh, available=False)
                    state.check()
                    reason, body = decision.reason, decision.body
                    if options.live and reason == 'no-injection' and delivery.available(session):
                        # Probe before repeating the final freshness and stop checks.
                        decision = policy.decide(session, result, state, cfg, options, clock.now(), fresh=fresh, available=True)
                        reason = decision.reason
                    need = decision.needs_user or decide.needs_user(result)
                    if need and reason == 'auto-reply-off':
                        reason = result.reason or ('reviewer-correction' if result['should_correct'] != 'no' else 'reviewer-needs-human')
                    if options.live and options.overnight and result['kind'] == '1' and reason != 'send':
                        need = True
                    if reason == 'send':
                        state.check()
                        count = max(session.auto_total, session.auto_turns, sum(row['session'] == session_key(session) for row in state.send_log.attempts(clock.now())))
                        state.send_log.reserve(session, clock.now(), options.max_turns)
                        # An interrupted attempt remains consumed and cannot be retried.
                        state.record(session, clock.now())
                        state.need(f'Needs you: {session.agent} {session.id[:8]}\nDelivery pending or interrupted; attempt consumed.', session)
                        success = delivery.send(session, policy.label(session, body, who, options.max_turns, count))
                        action, reason = ('sent', 'sent') if success else ('failed', 'send-failed')
                        if not success:
                            failed = need = True
                        if success:
                            options.sent += 1
                            counts['sent'] += 1
                    elif reason == 'dry-run':
                        action = 'would-send'
                        counts['would_send'] += 1
                    else:
                        action = 'flagged' if need else 'printed' if body else 'reviewed'
                    if body and action != 'sent' and not need and not args.json:
                        print(report.paste(body), file=out)
                    if not args.json:
                        print(report.audit(session, result, cfg['scrub'], who), file=out)
                        print('Action: ' + action + ' (' + reason + ')', file=out)
                except Exception as exc:
                    from .state import Stopped
                    if isinstance(exc,Stopped): raise
                    if not isinstance(exc,(ReviewError,LedgerError,ScrubUncertain)):
                        exc=ReviewError('reviewer failed: '+type(exc).__name__)
                    reason, action, need = str(exc), 'failed', True
                    if isinstance(exc, ScrubUncertain):
                        refused, action = True, 'refused'
                        state.session(session)['privacy_refusal'] = dict(evidence_key=(session.evidence_key), kind=exc.kind)
                    else:
                        failed = True
                    if not args.json:
                        print('Could not review: ' + session.id[:8] + ' ' + reason, file=out)
                if args.dry_run:
                    records.append(dict(session=session.id, action=action, reason=reason))
                    continue
                entry = decide.record(result,session,state,profiles.match(session,profs),cfg,
                                      action=action,reason=reason,body=body,needs_user=need)
                if need:
                    if not args.json: print(entry,file=out)
                retry = state.session(session).setdefault('review_tries',{})
                if result is None:
                    retry[session.turn_key]=retry.get(session.turn_key,0)+1
                if result is not None or action == 'refused' or retry.get(session.turn_key,0)>=3:
                    state.record(session, clock.now(), action == 'sent')
                else:
                    state.save()
                record = dict(time=clock.now().isoformat(), agent=session.agent, session=session.id[:8], cwd=session.cwd, turn_key=(session.turn_key), reviewer=who, action=action, reason=reason)
                if result:
                    record.update({k: result[k] for k in ('label', 'should_correct', 'kind', 'template', 'confidence', 'needs_user_topics')})
                    record['checks'] = result['checks']
                    if cfg['log']['text'] != 'none':
                        record.update({k: result[k] for k in ('summary', 'draft', 'question_for_user')})
                        record['body'] = body
                if cfg['log']['text'] == 'digest':
                    record['digest'] = block
                record = report.safe(record, cfg['scrub'])
                record['name'] = scrub(session.name or session.id,cfg['scrub'])
                if cfg['log']['text']=='none': record.pop('checks',None)
                state.log(record, cfg['log'])
                state.append('digest-'+clock.now().strftime('%Y-%m-%d')+'.md',report.daily(record)+'\n')
                records.append(record)
            counts['need_you'] = state.open_count()
            if args.json:
                print(json.dumps({**counts, 'results': records, 'needs_you_path': str(state.root / 'needs-you.md') if state.open_count() else None}), file=out)
            else:
                print(f'{clock.now():%H:%M}  {counts["in_scope"]} in scope · {counts["reviewed"]} reviewed · {counts["sent"]} sent · {counts["would_send"]} would send · {counts["need_you"]} need you', file=out)
            passes += 1
            if consent_needed or interval is None or args.max_passes and passes >= args.max_passes:
                break
            wake = clock.now() + dt.timedelta(seconds=interval)
            while clock.now() < wake and (not options.deadline or clock.now() < options.deadline):
                state.check()
                left = min((wake-clock.now()).total_seconds(), (options.deadline-clock.now()).total_seconds() if options.deadline else 2)
                clock.sleep(min(2, left))
        if state.open_count() and not args.json:
            print(str(state.root / 'needs-you.md'), file=out)
        return 5 if failed else 8 if refused else 7 if consent_needed else 10 if state.open_count() else 0
    finally:
        if lock:
            lock.close()
