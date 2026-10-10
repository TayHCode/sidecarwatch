from .scrub import ScrubUncertain
import argparse, os
from pathlib import Path
import sys
from . import APP_NAME, PREFIX, __version__
from . import config, profiles, report, sessions, digest, deliver
from .reviewer import ClaudeReviewer, CodexReviewer, REVIEWER_PROMPT, ReviewError, validate
from .state import Clock, State, Busy, Stopped, recent

class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs['allow_abbrev'] = False
        super().__init__(*args, **kwargs)
    def error(self, message):
        raise config.ConfigError(message)

HELP = {
    'config': 'Read this trusted configuration file.', 'claude_home': 'Read synthetic or local Claude session files here.',
    'codex_home': 'Read synthetic or local Codex session files here.', 'json': 'Print structured JSON output.',
    'version': 'Show the installed version.', 'target': 'Session ID, unique prefix, CLI name, or exact Codex name.',
    'only': 'Review scope: a profile name or nonempty folder; repeat to add scopes.',
    'since': 'Include activity since a duration, ISO timestamp or HH:MM.', 'agent': 'Select one session provider.',
    'window': 'Session discovery window, such as 24h.', 'all': 'Show every matching session.',
    'chars': 'Maximum characters shown per message.', 'tools': 'Include scrubbed tool-call summaries.',
    'reviewer': 'Choose the reviewing CLI; auto uses the other provider.',
    'list_only': 'List candidates or digest without a reviewer call or state change.',
    'profile': 'Read this trusted profile file.', 'session': 'Restrict to this session ID or unique prefix; repeatable.',
    'interval': 'Repeat after this duration; minimum 60s.', 'for_time': 'Stop after this duration; at most 24h.',
    'until': 'Stop at the next local HH:MM.', 'max_passes': 'Stop after this many passes.',
    'auto_reply': 'Enable or disable automatic template selection; default off.',
    'allow': 'Permit replies only inside this nonempty folder; repeatable.',
    'live': 'Select live delivery or print-only dry-run; default dry-run.',
    'overnight': 'Permit the fixed continuation template; human questions still block.',
    'max_turns': 'Automatic attempts per session lifetime; default 5, ceiling 50.',
    'match': 'Match part of a session name or working folder.',
    'long': 'Print a pasteable status block with the latest message.',
    'last': 'Show only this many final conversation entries.',
    'shapes': 'Show format names and counts without transcript values.',
}

def parser():
    common = Parser(add_help=False, argument_default=argparse.SUPPRESS)
    for opt in ('config', 'claude-home', 'codex-home'):
        common.add_argument('--' + opt)
    common.add_argument('--json', action='store_true')
    common.add_argument('--version', action='store_true')
    p = Parser(prog=APP_NAME, parents=[common])
    sub = p.add_subparsers(dest='command')
    parsers = [p]
    for name in ('sessions', 'show', 'audit', 'watch', 'stop', 'resume', 'rearm', 'doctor', 'status', 'init', 'demo', 'open', 'resolve'):
        cmd = sub.add_parser(name, parents=[common])
        parsers.append(cmd)
        if name in ('init', 'audit', 'watch'):
            cmd.add_argument('--allow-cross-vendor-review', action='store_true',
                             help='Explicitly enable digest review by the other vendor CLI; remember in your own config.')
        if name == 'rearm':
            cmd.description = 'Explicitly arm automatic accounting; preserve intact counts and save damaged files before recovery.'
        if name in ('show', 'audit', 'resolve'):
            cmd.add_argument('target', nargs=None if name=='resolve' else '?')
        if name in ('sessions', 'show', 'audit', 'watch'):
            cmd.add_argument('--only', action='append', default=[])
        if name in ('show', 'audit', 'watch'):
            cmd.add_argument('--since')
        if name in ('sessions', 'watch'):
            cmd.add_argument('--agent', choices=['claude', 'codex'])
        if name == 'sessions':
            cmd.add_argument('--window')
            cmd.add_argument('--all', action='store_true')
            cmd.add_argument('--match', default='')
            cmd.add_argument('--long', action='store_true')
            cmd.add_argument('--chars', type=int, default=500)
        if name == 'show':
            cmd.add_argument('--chars', type=int, default=1500)
            cmd.add_argument('--tools', action='store_true')
            cmd.add_argument('--last', type=int)
        if name in ('audit', 'watch'):
            cmd.add_argument('--reviewer', choices=['auto', 'claude', 'codex'], default='auto')
            cmd.add_argument('--list-only', action='store_true')
        if name == 'audit':
            cmd.add_argument('--dry-run', action='store_true', help='Print the digest locally; no reviewer or delivery calls.')
        if name == 'audit':
            cmd.add_argument('--profile')
        if name == 'watch':
            cmd.add_argument('--init', action='store_true', help='Mark current completed turns seen without reviewing or sending.')
            cmd.add_argument('--session', action='append', default=[])
            cmd.add_argument('--interval')
            end = cmd.add_mutually_exclusive_group()
            end.add_argument('--for', dest='for_time')
            end.add_argument('--until')
            cmd.add_argument('--max-passes', type=int)
            auto = cmd.add_mutually_exclusive_group()
            auto.add_argument('--auto-reply', action='store_true')
            auto.add_argument('--no-auto-reply', dest='auto_reply', action='store_false')
            cmd.set_defaults(auto_reply=False)
            cmd.add_argument('--allow', action='append', default=[])
            live = cmd.add_mutually_exclusive_group()
            live.add_argument('--live', action='store_true')
            live.add_argument('--dry-run', action='store_true', help='Strictly local digest preview; no reviewer or delivery calls.')
            cmd.set_defaults(live=False, dry_run=False)
            cmd.add_argument('--overnight', action='store_true')
            cmd.add_argument('--max-turns', type=int)
        if name == 'doctor':
            cmd.add_argument('--shapes', action='store_true')
    for current in parsers:
        for action in current._actions:
            if action.help is None and action.dest in HELP:
                action.help = HELP[action.dest]
    return p

def main(argv=None, *, stdout=None, stderr=None, clock=None, reviewer=None, delivery=None):
    out, err = stdout or sys.stdout, stderr or sys.stderr
    clock = clock or Clock()
    try:
        p = parser()
        # argparse's help uses real streams; redirect only for this synchronous call.
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = out, err
            args = p.parse_args(argv)
        except SystemExit as exc:
            return int(exc.code)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        if getattr(args, 'version', False):
            print(APP_NAME, __version__, file=out)
            return 0
        if args.command is None:
            p.print_help(out)
            return 0
        args.json = getattr(args, 'json', False)
        cfg_path, config_dir, state_dir, homes = config.directories(args)
        cfg = config.load(cfg_path, bool(getattr(args, 'config', None)))
        profs = profiles.load(config_dir, getattr(args, 'profile', None))
        state, now = State(state_dir, clock), clock.now()
        if args.command in ('audit', 'watch', 'rearm') and not getattr(args, 'list_only', False) and os.environ.get(PREFIX + 'ACTIVE') == '1':
            raise config.ConfigError('nested-run')
        if args.command == 'demo':
            from .demo import run
            return run(cfg, out, clock)
        if args.command == 'init':
            for name, target in [('config.example.toml', config_dir / 'config.toml'), ('profile.example.toml', config_dir / 'profiles/example.toml')]:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    with target.open('x') as f:
                        f.write((Path(__file__).parent / 'data' / name).read_text())
                    target.chmod(0o600)
                    print('Wrote ' + str(target), file=out)
                except FileExistsError:
                    print('Skipped ' + str(target), file=out)
            if getattr(args, 'allow_cross_vendor_review', False):
                print('Enabling review: a scrubbed digest of finished turns, recent turn text, tool summaries and your profile '
                      'will go from Claude Code to the Codex CLI (OpenAI), or from Codex to the Claude CLI (Anthropic).', file=out)
                print(report.PRIVACY_RESIDUE, file=out)
                config.remember_consent(cfg_path)
            enabled = config.load(cfg_path)['privacy']['allow_cross_vendor_review']
            print('Cross-vendor review: ' + ('ON; audit/watch may send a digest when local checks pass.' if enabled else
                  'OFF; nothing leaves your machine. Use init --allow-cross-vendor-review to consent.'), file=out)
            print('Optional note for agents: report completion and missing input honestly.\nA TURN-STATUS line never authorizes an automatic reply.\nNormal CLI completion and clean whole-turn evidence remain required.',file=out)
            return 0
        if args.command == 'status':
            value = {'cross_vendor_review': cfg['privacy']['allow_cross_vendor_review'], 'config': str(cfg_path), 'kill_switch': state.stopped(), 'need_you': state.open_count()}
            import json
            print(json.dumps(value) if args.json else 'Cross-vendor review: ' + ('ON' if value['cross_vendor_review'] else 'OFF') +
                  '\nConfig: ' + str(cfg_path) + '\nKill switch: ' + ('on' if state.stopped() else 'off') +
                  '\nNeeds you: ' + str(value['need_you']) + ' open; run open to inspect.', file=out)
            return 10 if state.open_count() else 0
        if getattr(args, 'allow_cross_vendor_review', False):
            if getattr(args, 'dry_run', False) or getattr(args, 'list_only', False) or getattr(args, 'init', False):
                raise config.ConfigError('local preview/init does not enable review; use init --allow-cross-vendor-review separately')
            print('Explicit consent: scrubbed digests of finished turns, recent turn text, tool summaries and your profile '
                  'may be sent to the Claude CLI (Anthropic) or Codex CLI (OpenAI). Choice saved in your own config.', file=err)
            print(report.PRIVACY_RESIDUE, file=err)
            config.remember_consent(cfg_path)
            cfg['privacy']['allow_cross_vendor_review'] = True
        notes = []
        if args.command == 'doctor':
            items = sessions.discover(homes, now, cfg['discovery'], state_dir, notes)
            for note in state.unseen_notes(notes):
                print(note, file=err)
            print(report.doctor(cfg, state, homes, cfg_path, config_dir, items, args.json, args.shapes), file=out)
            return 0
        if args.command == 'rearm':
            with state.acquire():
                state.send_log.arm(now)
            print('Automatic accounting armed; existing valid counts retained. Live sends still require all three switches.', file=out)
            return 0
        if args.command in ('stop', 'resume'):
            if args.command == 'stop':
                state.write('STOP', '')
            else:
                (state.root / 'STOP').unlink(missing_ok=True)
            print(str(state.root / 'STOP') if args.command == 'stop' else 'Kill switch cleared: ' + str(state.root / 'STOP'), file=out)
            return 0
        if args.command == 'open':
            state.sync_needs()
            print((state.root/'needs-you.md').read_text(),file=out)
            return 10 if state.open_count() else 0
        if args.command == 'resolve':
            with state.acquire():
                name=state.resolve(args.target)
            print('Resolved '+name,file=out)
            return 0
        if args.command == 'watch':
            from . import watch
            return watch.watch(args, cfg, profs, homes, state, clock, out, err, reviewer, delivery)
        if args.command in ('sessions', 'show', 'audit'):
            if getattr(args, 'window', None):
                cfg['discovery']['window_hours'] = config.duration(args.window) / 3600
            items = sessions.scope(sessions.discover(homes, now, cfg['discovery'], state_dir, notes), args.only, profs)
            for note in state.unseen_notes(notes):
                print(note, file=err)
            if getattr(args, 'agent', None):
                items = [s for s in items if s.agent == args.agent]
            if args.command == 'sessions':
                if args.chars < 1:
                    raise config.ConfigError('--chars must be positive')
                items = [s for s in items if args.match.lower() in (s.name + ' ' + s.cwd).lower()]
                chosen = items if args.all else items[:20]
                text = report.status_blocks(chosen, profs, cfg['scrub'], now, args.chars) if args.long and not args.json else report.listing(chosen, profs, cfg['scrub'], now, args.json)
                print(text, file=out)
                return 0
            if args.only and args.target is None:
                if len(items) > 1:
                    raise LookupError('ambiguous scope: ' + ', '.join(s.agent + ' ' + s.id for s in items) + '; use an exact ID from sessions')
                session = sessions.target(items, items[0].id if items else None)
            else:
                session = sessions.target(items, args.target)
            since = config.when(args.since, now) if args.since else None
            if args.command == 'show':
                if args.chars < 1 or args.last is not None and args.last < 1:
                    raise config.ConfigError('--chars and --last must be positive')
                print(report.conversation(session, cfg['scrub'], since, args.chars, args.tools, args.json, args.last), file=out)
                return 0
            if args.command == 'audit':
                if session.state == 'unknown':
                    raise ReviewError('format-not-recognised: run "' + APP_NAME + ' doctor --shapes"')
                profile = profs[0] if args.profile else profiles.match(session, profs)
                block = digest.build(session, profile, cfg, since=since)
                if args.list_only or args.dry_run:
                    print(block, file=out)
                    return 0
                state.check()
                calls = recent(state.data['reviews'], now, 3600)
                if len(calls) >= cfg['reviewer']['max_per_hour']:
                    raise ReviewError('review-budget')
                who = ('claude' if session.agent == 'codex' else 'codex') if args.reviewer == 'auto' else args.reviewer
                if not cfg['privacy']['allow_cross_vendor_review']:
                    print(report.review_notice(who, cfg), file=err)
                    return 7
                service = reviewer or (ClaudeReviewer if who == 'claude' else CodexReviewer)(cfg, state)
                if reviewer is None:
                    service.check()
                print(f'Sending a scrubbed digest of {session.agent} session {session.id[:8]} to {who} for review.', file=err)
                state.data['reviews'] = calls + [now.timestamp()]
                state.save()
                from . import decide
                result = decide.context(validate(service.review(REVIEWER_PROMPT, block), cfg['scrub'], profile), session, cfg['scrub'])
                with state.acquire():
                    entry = decide.record(result, session, state, profile, cfg, action='reviewed',
                                          reason=result.reason or ('reviewer-correction' if result['should_correct'] != 'no' else 'reviewer-needs-human'),
                                          body='', needs_user=decide.needs_user(result))
                print(report.audit(session, result, cfg['scrub'], who, args.json), file=out)
                if entry and not args.json:
                    print(entry, file=out)
                if state.open_count() and not args.json:
                    print('Needs you: ' + str(state.open_count()) + ' open; run open to inspect.', file=out)
                return 10 if state.open_count() else 0
        raise config.ConfigError('unknown command')
    except ScrubUncertain as exc:
        print(str(exc), file=err)
        return 8
    except ReviewError as exc:
        print(str(exc), file=err)
        return 5
    except config.ConfigError as exc:
        print(str(exc), file=err)
        return 2
    except LookupError as exc:
        print(str(exc), file=err)
        return 4
    except Busy as exc:
        print(str(exc), file=err)
        return 6
    except Stopped as exc:
        print(str(exc), file=err)
        return 3
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if os.environ.get(PREFIX + 'DEBUG') == '1':
            raise
        print('internal error: ' + type(exc).__name__, file=err)
        return 1

def entry():
    sys.exit(main())
