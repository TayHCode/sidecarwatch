# Codex lanes watcher plan

*Historical design. Status at writing: planned, not built.*

## The problem

The owner runs many coding agent sessions at once across several projects.
Planning happens in separate planning chats.
The owner was the only connection between them, copying each worker's
receipt to a coordinator and each coordinator's answer back to a worker.
The copying itself added nothing. The valuable parts were the owner's decisions
and the occasional "are we still on track?"

## What already exists (built 2026-09-28)

The scripts are on PATH. They cost no
tokens and call no model.

| Tool | What it does |
|---|---|
| `lanes [--hours N] [--match text]` | One status block for every recent Codex session: running/idle, age, directory, last agent message. Paste it into a coordinator chat. |
| `lanes "exact name"` | The full last message from one session. |
| `thread "exact name" [--since HH:MM]` | A session's user and agent messages in order, for audits. |
| `send "exact name" "message"` | Queues a message into a session (`codex queue`). Starts a turn even if the session is idle. |

Transcripts are in `~/.codex/sessions`, names in
`~/.codex/session_index.jsonl`.

Rule for every queued message: it appears exactly as if the owner typed it, so it
always starts "From the owner via …".

## The plan

A first reviewer does the noticing. A second reviewer is consulted only when the
cheap model isn't sure. the owner keeps every decision.

```
each pass (the original ran every 15 minutes)
  → lanes-style check (no model): which sessions finished a turn since last run?
  → nothing new: stop (most runs, free)
  → for each finished turn: cheap model reads the final message + rubric
      → DONE / WORKING / BLOCKED      → daily digest only
      → NEEDS_USER (a real question)        → push notification with the question
      → UNSURE / DRIFT / RISK                 → ask second reviewer, then notify the owner with second reviewer's answer
```

### Roles

| Who | Does | Never does |
|---|---|---|
| Scripts | Detect finished turns, collect text | Judge anything |
| First reviewer | Classify each finished turn, draft plain summaries, flag mechanical problems | Decide, approve, send instructions |
| second reviewer | Drift, architecture and provider questions, audits before merge or release, anything the cheap model marks unsure | Act on the owner's behalf without the owner's yes |
| the owner | Every decision; approves every scope-changing message | Copy and paste routine traffic |

### Classification rubric for the cheap model

For each finished turn, return exactly one line:

- `DONE: <what finished, PR/issue link>`
- `WORKING: <what it's doing next>` (no action needed)
- `NEEDS_USER: <the exact question or action, one sentence>`
- `BLOCKED: <which lane/fact it waits for>`
- `UNSURE: <why>`: escalate to second reviewer

Also flag, as `UNSURE`, any of these seen today:

- a claim that doesn't match reality (e.g. "paused" before the pause was confirmed)
- a production release, deploy, or flag change
- a decision recorded "in the owner's name" that the owner may not have made
- work drifting from the lane's stated goal
- repeated rate limiting or a job running far longer than expected
- a new provider, account, credential, DNS change or external message

### What gets automated, in phases

**Phase 1: notify-only (first).** Classify, write the daily digest, push
notification only for `NEEDS_USER` and escalated items. Nothing is sent to
workers. Run it for one week.

**Phase 2: forward plain facts (only if Phase 1 is trusted).** Allow automatic
`send` of purely factual notes, e.g. "The export test passed."
Still no decisions, approvals or scope changes.

**Never automated:** the owner's decisions, messages that change a lane's scope,
authority, releases, production flags, provider writes, or external
communication. A model relaying "approved, proceed" between workers is how the
earlier orchestration attempts went wrong.

## Guardrails (lessons from earlier orchestration attempts)

1. Observe and notify; don't orchestrate. No model starts, stops, or directs
   workers on its own.
2. No new service, database, queue or dashboard for this. A script,
   a rubric and a notification.
3. Every automated message says where it came from.
4. If the watcher becomes the project, stop and delete it.

## Cost (rough)

Most runs call no model; a classification is small.

## How to judge Phase 1 after a week

Keep going only if all are true:

- It never missed something the owner needed to see.
- Notifications were worth reading (few false alarms).
- Escalations to second reviewer were genuinely hard cases.
- the owner spent noticeably less time copying and checking.

Stop or redesign if it misses things, nags, or starts needing its own
maintenance.

## Open questions

- Model: first reviewer. Confirm in the trial that
  its classifications are reliable; only the model name changes if that shifts.
- Billing: API key vs. Claude Code headless on the owner's plan.
- Notification channel: a pinned digest file.


## What changed since this design

The public package keeps the six checks, labels, Kind 1 and Kind 2, recorded
decisions, current questions and daily digest. It reads both CLI formats and
can audit a running session. Reviews are isolated per session. Drafts are for
the user; automated replies use a closed set of sentences. Human questions and
uncertain turns stop automation. Whole-turn event evidence, scrubbing, explicit
switches, durable attempt limits and a kill switch guard every automatic reply.
The second tool-using reviewer, free-text message command, attachment reads,
remote machines, session launchers and notification hooks are absent.
The old commands above are historical notes; use the commands in the README.
