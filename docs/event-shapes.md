# Structured continuation evidence

This is the supported transcript grammar. A keys/types-only inspection of three real local files per CLI informed the envelope and message metadata listed below. No message values or real session content were copied into fixtures or documentation. Tests use invented values with these structures; actual vendor review/delivery remains unverified. Observational parsing can display more shapes than this grammar can authorize. Missing fields, unrecognized events, ambiguous ordering or malformed records never authorize continuation.

Unknown top-level envelope fields do not make a turn unsupported. They are still scanned for diagnostic failures and cannot certify completion. Unlisted message, item, content-block and turn-payload fields withhold automatic continuation. Codex session metadata and turn context may contain extra metadata fields; they cannot certify completion. Recognized diagnostic flags are still checked even on a supported row. Session IDs cannot change within one file. Optional Codex `turn_id` values must agree throughout the turn, including its completion; a missing terminal ID after a supplied start ID is ambiguous. Boolean diagnostic flags cannot be null or strings. An in-progress terminal status is contradictory. Tool-result bodies must be strings (or Claude text blocks); unknown content-block types are refused.

All rows require valid timezone-bearing ISO timestamps. Timestamps must be nondecreasing and not in the future. The completion timestamp must be strictly after the latest human or automatic message. A later row invalidates the old completion, even a benign progress or duplicate terminal record. A new human turn must acquire its own complete evidence. No quiet-time fallback grants eligibility. Watch withholds reviews for invalid timestamps in both local dry-run and live modes.

## Codex

Every row has string `type` and `timestamp`, and an object `payload`. Observed envelope metadata includes integer `ordinal` and object `metadata`; additional envelope keys are tolerated.

Observed `session_meta.payload` fields include string `id`, `cwd`, `source`, `originator`, `cli_version`, `model_provider`; objects `git`, `base_instructions`; and list `tools`. Observed `turn_context.payload` includes string `cwd`, `model`, `effort`, `approval_policy`; objects `sandbox_policy`, `collaboration_mode`, and instruction/context metadata. Only the identity/cwd fields are used for observation; all metadata remains subject to failure checks.

User messages accept optional lists `images`, `local_images`, `text_elements`. Agent messages accept optional string `phase`; task starts accept `collaboration_mode_kind`, `model_context_window`; completion accepts integer `completed_at_ms`. Response messages accept `id`, `phase`, `end_turn`; item messages accept `id`, `phase`, `delivery`. Text blocks accept an empty `text_elements` list. Reasoning may carry `id` and `encrypted_content`; it cannot authorize a turn.

| Row | Required content |
| --- | --- |
| `session_meta` | String `id` and `cwd`, before a turn; subagent sessions remain excluded |
| `turn_context` | String `cwd`; activity after completion invalidates it |
| `event_msg` / `task_started` or `turn_started` | Opens a turn; duplicate starts within one turn are inconsistent |
| `event_msg` / `user_message` | String `message` |
| `event_msg` / `item_completed` | `item.type=UserMessage` or `AgentMessage`; string content or text blocks |
| `event_msg` / `agent_message` | String `message`; an actual agent message is required |
| `response_item` / `message` | `role=user` or `assistant`; string content or `input_text`, `output_text`, `text` blocks |
| `response_item` / `function_call`, `custom_tool_call`, `local_shell_call` | Nonempty unique `call_id` and string/object arguments, input or action |
| Corresponding `_output` | Matching `call_id`, output, and explicit outcome metadata |
| `event_msg` / `task_complete` or `turn_complete` | Terminal signal; any `last_agent_message` must equal the actual final text |
| `event_msg` / `agent_reasoning`, `token_count`; `response_item` / `reasoning` | Scanned in full; later activity invalidates completion |

Initial injected environment/instruction response items are metadata, never human authorization. Only adjacent identical user representations from different sources at the same timestamp are treated as aliases by the evidence check. A later user copy cannot be dismissed as stale decoration. The separate marker accountant still deduplicates copied fixed automatic frames conservatively for caps.

## Claude Code

Observed envelope fields include strings `sessionId`, `cwd`, `uuid`, `requestId`, `gitBranch`, `version`, `userType`, `permissionMode`, `entrypoint`, `promptId`; nullable string `parentUuid`; booleans `isSidechain`, `isMeta`; and object `turnPosition`. Unknown envelope keys are tolerated. `sessionId` must be a nonempty string and stable throughout the file. It is identity metadata, never review text.

Observed assistant message metadata includes string `id`, `model`, `role`, `type`, `stop_reason`; object `usage`; nullable `stop_sequence`, `stop_details`, `container`, `context_management`, `diagnostics`; and list `input_transformations`. Supported assistant blocks may carry object `caller` in addition to their core fields. All these metadata fields are checked for diagnostic vetoes.

A user row has `type=user`, a timestamp and `message.content` as a string or text blocks. An assistant row has `type=assistant` and string content or a list of `text`, `thinking`, and `tool_use` blocks. Tool blocks require unique `id` and object `input`. User tool-result blocks require matching `tool_use_id`, content, and explicit outcome metadata.

Only assistant `message.stop_reason=end_turn`, with actual agent text and no pending tools, certifies normal completion. `tool_use` continues the turn. `stop_sequence`, a duration record, a summary, or silence does not certify it. An interrupted-user marker, unknown content block, sidechain/compaction record within the observed file, or unfamiliar system event withholds continuation. `system/turn_duration` is understood as observational metadata; it cannot replace `end_turn`, and a later duration row invalidates the earlier completion.

## Outcomes and whole-turn vetoes

A matched tool result needs at least one explicit CLI outcome: integer zero `exit_code`, `exitCode` or `returncode`; `is_error=false`; or `status=success`, `completed` or `ok`. Any contradictory field blocks it. A string containing “success” is never an outcome. Omitted status, orphan results, repeated IDs/results and pending calls fail closed. Tools must be followed by an actual agent message before completion.

The complete row, including nested metadata, is checked before display cuts. Error/failure/abort/interruption/retry/permission-denial flags, nonzero or wrongly typed exit values, unsuccessful outcomes and unsupported status values veto the turn. Tool-result JSON strings are decoded with the same bounds and checked too. Text diagnostics include signed exits, assertion errors, fatal failures, failing tests and permission denial. Earlier failures remain blocking after a successful retry, a later successful tool or a success final. Subagent results carried in a parent tool are subject to the same checks; unknown subagent event shapes fail closed.

Prose is only a veto: questions, permission requests, risk terms, failure diagnostics, unsupported alphabets and control characters can prevent delivery. No vocabulary of positive final statements exists. These text rules intentionally produce false positives and are not a general language interpreter. The normal CLI terminal event establishes only that a supported turn ended normally; it cannot prove task correctness or safety of the agent's existing permissions.

Before delivery the entire file is parsed again and its content fingerprint, identity, folder, turn and structured evidence must still agree. Capability probing is followed by another check. A file change after that final check remains a local race; this tool does not lock or own the worker CLI.

## Invalid input and visibility

JSON depth is capped at 32, lines at 1,048,576 decoded characters and strings at 262,144 characters. Duplicate keys, lone surrogates, NUL, replacement characters and oversized values are rejected. Valid surrounding rows remain readable. A visible note identifies the skipped line without showing its content. An incomplete transcript is never sent for review or automatically resumed. Marker counters are length-bounded before numeric conversion.

Worker failures are recorded before the finished-turn review filter. They require no reviewer call or automatic reply to appear in `needs-you.md` and the action log. The final deadline pass records unfinished workers or missing responses. Existing unresolved reasons survive later runs.

## Explicit compatibility limits

The supported completed review fixtures are Claude user → assistant with `end_turn`, and Codex session metadata → task start → user/agent text → task completion, with the metadata above. Existing text/item/response variants and paired successful tool shapes in the tables remain supported. Extra top-level envelope fields alone never disable a turn.

Structure inspection also found shapes this grammar cannot certify: Claude attachment records, display/tool-reference content blocks and unfamiliar system events; Codex `item_completed` tool/question variants and list-valued tool outputs. Nonempty text-element annotations and unknown nested message/payload fields remain unsupported. They may be shown observationally, but withhold automatic replies with `unsupported-turn-data` (an unsupported event/message shape). Unknown event types still fail closed. Real sessions containing these shapes are not claimed to support automatic continuation. Local display does not establish reply eligibility.

Corrupt/oversized-file diagnostics include the file path and line number, never its content. A local fingerprint cache reports each unchanged issue once across commands; changed files are reported again. Listing/show remain locally readable, and incomplete transcripts refuse outgoing review.

With explicit review consent, `audit` and eligible finished-turn watch reviews may still send scrubbed observable text from unsupported shapes. Shape restrictions block automatic replies; malformed or incomplete transcripts and privacy refusals block review too.

## Reviewer reply schema

The reviewer returns one JSON object with exactly these keys. Every field must be present; extra keys are rejected, including `escalate_reason`. Use `sidecarwatch.rubric.schema()` for the executable schema and `sidecarwatch.reviewer.validate()` to validate replies. Display labels such as “On task” are not JSON keys.

| Key | JSON type and allowed values |
| --- | --- |
| `summary`, `correction_reason`, `draft`, `question_for_user` | Strings; use `""` when empty |
| `label` | `"DONE"`, `"WORKING"`, `"NEEDS_USER"`, `"BLOCKED"`, `"UNSURE"` |
| `checks` | Object with exactly `on_goal`, `in_scope`, `kiss`, `not_rebuilding`, `root_cause`, `honest` |
| Each check | Object with exactly `rating` (`"ok"`, `"concern"`, `"na"`) and `note` (string) |
| `should_correct` | `"no"`, `"maybe"`, `"yes"` |
| `kind` | String `"1"`, `"2"`, `"none"` |
| `template` | `"none"`, `"back_to_goal"`, `"already_decided"`, `"show_evidence"`, `"keep_it_simple"`, `"run_tests"`, `"summarize"`, `"housekeeping"`, `"continue"` |
| `decided_index` | Integer (never a boolean); zero-based profile decision for `already_decided`, otherwise `-1` |
| `needs_user_topics` | Array of `"decision"`, `"scope_change"`, `"money"`, `"credentials"`, `"release"`, `"other"`; may be empty |
| `worker_pushed_back` | Boolean `true` or `false` |
| `confidence` | `"low"`, `"medium"`, `"high"` |

This complete valid reply describes ordinary progress and is exercised by the documentation regression test and fake reviewer CLI:

```json
{
  "summary": "Checking CSV export.",
  "label": "WORKING",
  "checks": {
    "on_goal": {"rating": "ok", "note": ""},
    "in_scope": {"rating": "ok", "note": ""},
    "kiss": {"rating": "ok", "note": ""},
    "not_rebuilding": {"rating": "ok", "note": ""},
    "root_cause": {"rating": "ok", "note": ""},
    "honest": {"rating": "ok", "note": ""}
  },
  "should_correct": "no",
  "correction_reason": "",
  "kind": "none",
  "template": "none",
  "decided_index": -1,
  "needs_user_topics": [],
  "worker_pushed_back": false,
  "draft": "Summarize the export work.",
  "question_for_user": "",
  "confidence": "high"
}
```

Kind 1 needs a fixed template and no new decision topics. Kind 2 carries the owner's question and unsent draft. Low confidence, a question, unresolved labels or decision topics can promote a schema-valid reply to Kind 2. A worker question can also require input even if the reviewer returns the clean example above. A valid reply grants no delivery permission. Free text is scrubbed and checked before display or persistence.

For local integration tests, configure fake `claude`/`codex` commands in a throwaway HOME. A fake Claude command returns this object inside `{"structured_output": <reply>}`; a fake Codex command writes this object to the file named by `-o` (the output-last-message option). Both must advertise the non-interactive/schema flags probed by `doctor`. The synthetic commands in `tests/fixtures/bin` show the full adapter protocol. Never use real session homes for these tests.
