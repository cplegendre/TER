# Claude Code hooks guide

Claude Code runs *hooks*: shell commands it calls at points in a session,
passing a JSON payload on stdin. TER uses hooks in three ways:

| Use | Command | What it does | Changes agent behaviour? |
|---|---|---|---|
| **Capture** (TER 4, L1) | `python -m ter hook` | Records each prompt and tool call as a `ter.event` in an event log, for live or later analysis | No: always answers `{}` |
| **Live waste monitor** (TER 3) | `ter hook monitor` | Checks five fast patterns on each tool call and injects guidance when one trips | Yes: adds context for the agent and a notice for you |
| **Countermeasure hooks** (from the A3) | scripts the A3 writes for you | Block or flag a specific waste the A3 found in your sessions | Yes: blocks or feeds back on the call |

The TER 3 monitor's full reference, with every threshold, remains in
[docs/hooks-guide.md](../hooks-guide.md).

## Capturing sessions with the TER 4 hook

The capture hook turns Claude Code hook payloads into the same event stream
TER builds from transcripts, so a live session and its transcript go through
one analysis engine ([docs/ter4/l1-observed.md](../ter4/l1-observed.md)).

### 1. Register the hook

Add to `.claude/settings.json` in your project (or `~/.claude/settings.json`
for every project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python -m ter hook"}]}
    ],
    "PreToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "python -m ter hook"}]}
    ],
    "PostToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "python -m ter hook"}]}
    ]
  }
}
```

`python` must be the interpreter TER is installed in. If it is in a virtual
environment, use its absolute path, for example
`/path/to/venv/bin/python -m ter hook`.

PostToolUse alone is enough to record tool calls: it emits the request and
the completion. Registering PreToolUse too records the request before the
tool runs; the duplicate request that PostToolUse emits later has the same id
and is discarded (TER-OBS-004).

### 2. Choose where events go

Events are appended as JSONL, one file per session, under
`$XDG_CACHE_HOME/ter/events` (`~/.cache/ter/events` when `XDG_CACHE_HOME` is
unset) by default. The log holds your prompts and tool output, so it lives in
your home directory rather than a shared temporary directory. Move it with an
environment variable or a flag:

```bash
export TER_EVENT_LOG_DIR="$HOME/.ter/events"
python -m ter hook --event-log "$HOME/.ter/events" < payload.json
```

### 3. Check it works

Replay the fixture payloads the contract tests use, then read the log back:

```bash
python -m ter hook --event-log /tmp/ter-demo < tests/fixtures/hooks/user_prompt_submit.json
python -m ter hook --event-log /tmp/ter-demo < tests/fixtures/hooks/post_tool_use_read.json
python -m ter hook --event-log /tmp/ter-demo < tests/fixtures/hooks/post_tool_use_edit.json
python -m ter observe --event-log /tmp/ter-demo
```

Each hook call prints `{}` and exits 0. `observe` then shows the observables:

```text
TER observe · session 3f0c9a1e-hook-demo
  events             5  (generated 2 · tool 2 · user 1)
  by kind            intent.stated 1 · tool.completed 2 · tool.requested 2
  by tool            fs.edit 1 · fs.read 1
  ...
```

With one session in the log, `observe` picks it; with several, it lists them
and asks for `--session`.

### 4. Analyse what was captured

```bash
python -m ter observe --event-log "$HOME/.ter/events"                   # lists sessions if there are several
python -m ter observe --event-log "$HOME/.ter/events" --session SESSION_ID --timeline
python -m ter observe --event-log "$HOME/.ter/events" --session SESSION_ID --json
```

For the Lean explanation and the A3, analyse the session's transcript
(`~/.claude/projects/<project>/<session>.jsonl`): hooks carry no reasoning or
responses, so the planning, restated-reasoning and unvalidated-response
detectors need the transcript.

```bash
ter a3 ~/.claude/projects/my-project/SESSION_ID.jsonl --html a3.html
```

### Which hook events are recorded

| Hook | Recorded as |
|---|---|
| `UserPromptSubmit` | `intent.stated` (the prompt) |
| `PreToolUse` | `tool.requested`, with the tool kind and input |
| `PostToolUse` | `tool.requested` (same id as PreToolUse) and `tool.completed` with the tool response |
| `Stop` | `task.completed`: the agent finished a turn, keyed by the turn it closes |
| `SubagentStop` | `subagent.completed`, in the parent session, keyed by the payload's `agent_id` |
| `SessionStart`, `SessionEnd`, `SubagentStart`, `PreCompact`, `Notification` | recognised as lifecycle; no event |
| anything else, or a malformed payload | ignored, with a reason |

Lifecycle events are counted (class `lifecycle`) but never scored, and they
add no step to the Lean analysis. The Stop and SubagentStop mappings follow
Claude Code's documented payloads. On real recordings (9 October 2026, two
Claude Code sessions on Windows) every Stop matched the transcript's stop;
SubagentStop is the one kind still to be checked: see
[Checking recordings against transcripts](#checking-recordings-against-transcripts).

### Recording real payloads

`--record DIR` saves every payload the hook reads, before handling it, to
`DIR/<session>/<seq>-<hook>.json`, byte for byte, with the time it arrived (`<seq>` is the write time in nanoseconds, so names sort in arrival order). Recordings are
how the hook shapes TER relies on get checked against real runs (issue #35):

```bash
python -m ter hook --event-log "$HOME/.ter/events" --record "$HOME/ter-data/hooks"
```

Register that command for every hook you want captured (`Stop`,
`SubagentStop`, `SessionStart`, `PreCompact` and the rest, not only the tool
hooks). Recordings hold full tool inputs and output, so they are private to
your user (mode 0600), stay outside the repository, and go through redaction
before any of them becomes a test fixture. A recording that cannot be written
is reported on stderr as `ter hook: payload not recorded: <reason>`; the
event is still handled and the hook still prints `{}`.

### Checking recordings against transcripts

Once you have recorded a few sessions, check how the events the hook derives
line up with the events TER reads from the same sessions' transcripts:

```bash
python -m ter hooks check "$HOME/ter-data/hooks" ~/.claude/projects --json hooks-check.json
```

The first argument is the `--record` directory; the second is the Claude Code
projects folder (or any folder of `.jsonl` transcripts, or one transcript).
Each recorded session's transcript is the payloads' `transcript_path` when
that file exists, else the `<session_id>.jsonl` found under the second
argument. For each session the check:

1. counts the payloads by hook and lists the payload **field names** seen per
   hook;
2. replays every recording through the path the live hook takes (same
   translation, same receive time, the Stop's turn looked up in the
   transcript), giving the hook-derived events;
3. reads the transcript through the session source, giving the
   transcript-derived events;
4. reports, per event kind, how many hook events have an event with the same
   id in the transcript stream (TER-OBS-007), their ids, and for each one
   that does not, why: no transcript; the hook fell back to its own key (a
   prompt whose record was not in the transcript when the hook ran, a tool
   call without `tool_use_id`, a stop with no turn, a SubagentStop without
   `agent_id`); same `tool_use_id` (or same prompt text) but a different id
   rule; for a SubagentStop, no subagent transcript for its `agent_id` or one
   that shows no finish; the session source derives no events of that kind;
   or no counterpart at all;
5. reports, for each Stop payload, whether its `task.completed` id equals the
   id the session source derives for the same stop (TER-OBS-005).

```text
TER hooks check · 1 session(s)
  hook events matching a transcript event id  3/4 (75.0%)   (TER-OBS-007)
  Stop payloads matching the transcript stop  1/1 (100.0%)   (TER-OBS-005)

session 3f0c9a1e-hook-demo
  transcript     transcripts-dir
  payloads       PostToolUse 1 · PreToolUse 1 · SessionStart 1 · Stop 1 · UserPromptSubmit 1
  ...
    intent.stated       0/1 matched · 1 transcript-only
      1 × hook prompt keyed by its text: its transcript record was not there when the hook ran (written later, or no transcript_path)
    tool.requested      1/1 matched · 0 transcript-only
```

The report is content-free: counts, event ids (hashes), hook names, field
names and fixed reason strings. It never prints a prompt, a tool input or a
tool output, so you can share it (or its JSON) in issue #35 when the
recordings themselves must stay private. It exits 0 whatever it finds, and 2
only when the recordings or the transcripts folder cannot be read.

How a stop is matched: Claude Code writes a `system` record with subtype
`stop_hook_summary` after the Stop hooks ran. Both sides key the stop by the
turn it closes, the last main-chain `assistant` record before it: the Stop
hook reads the tail of `transcript_path` for the last such record written by
the time the payload arrived, and the session source takes the last one
before each `stop_hook_summary`. Both then use
`make_event_id(session_id, turn_uuid, "stop", "task.completed")`
(`ter/adapters/claude_code_turns.py`). If the hook cannot read the
transcript, the stop is keyed by the second it arrived, as before, and the
check reports it as unkeyed.

Prompts and tool calls follow the same principle, one id rule per kind
shared by both sides (`ter/adapters/claude_code_ids.py`; the full table is in
[L1 Observed](../ter4/l1-observed.md#shared-id-rules-ter-obs-007)):

- **Tool calls** are keyed by session + `tool_use_id` + kind. Pre/PostToolUse
  payloads and the transcript's `tool_use`/`tool_result` blocks all carry the
  `tool_use_id`. A transcript block without one (older transcripts) keeps the
  record rule (uuid + block index), and a hook payload without one is keyed
  by its input; neither can match.
- **Prompts** are keyed by the transcript record that holds them: the hook
  reads the last 1 MiB of `transcript_path` for the last main-chain `user`
  record (or queued-prompt attachment) with the same text written by the
  time the payload arrived, and uses that record's uuid and block, as the
  session source does. The id never depends on the text, so redacted
  sessions keep their ids. When no record holds the prompt yet, the prompt
  keeps its text-and-second key and the check reports it as unkeyed.

- **Subagents** are keyed by session + `agent_id`. Claude Code writes each
  subagent's transcript to `<session id>/subagents/agent-<agent_id>.jsonl`
  beside the session's own; the session source derives one
  `subagent.completed` for each such file that shows the subagent finished
  (its own last turn ended with no tool call pending, the parent's Agent
  result reports it completed, or the parent was notified that the
  background agent completed), at the time of the latest such marker. A
  subagent resumed and stopped again keeps its one id. A SubagentStop
  without `agent_id` is keyed by when it arrived and cannot match.

What real recordings showed (9 October 2026, Claude Code on Windows, two
sessions; summary in `tests/fixtures/hooks/real-check-2026-10-09.md`): 87 of
94 hook events matched a transcript event id. Every prompt (11), tool
request (34), tool completion (32) and stop (10 of 10, TER-OBS-005) matched,
so each prompt's transcript record was found written by the time its
`UserPromptSubmit` payload arrived. The 7 misses were all SubagentStop events, from before the session
source derived `subagent.completed`. TER-OBS-007 awaits one re-run of the
hooks check on the same machine with sessions that use subagents.

### Guarantees

- **Fail open.** The hook always prints `{}` and exits 0, even on a malformed
  payload or a full disk, so it can never block your session. When it cannot
  record an event it writes `ter hook: event not recorded: <reason>` to
  stderr, which Claude Code shows in verbose mode and its debug log.
- **Append-only.** Each hook process only appends to the session's log (the
  `RecordEvent` use case); it does not replay the session, so its cost does
  not grow as the session gets longer. A repeated record (PostToolUse
  repeating its PreToolUse request, a retried hook) is dropped by id when the
  log is analysed.
- **Light.** Appending a PostToolUse event takes under 50 ms at the 95th
  percentile, on a 2,000-event log (TER-OBS-003, benchmarked in
  `tests/unit/test_ter4_claude_hooks.py`). The hook entry point avoids
  importing the transcript reader and numpy.
- **Idempotent.** A redelivered event (same id) changes nothing (TER-OBS-004).
- **Passive.** While the maturity ceiling is L1, the hook returns an empty
  response (TER-OBS-008). Advisory interventions are L4 work.

Known limits: a prompt whose transcript record the hook cannot find yet is
keyed by its text and the second it was received (hooks carry no prompt id),
so the same text submitted twice counts twice, while one submission seen
twice within a second (the hook registered in two settings files) counts
once; such a prompt's id differs from the transcript's. Tool calls without a
`tool_use_id` are keyed by content. `python -m ter hooks check` shows which
ids match.

## The live waste monitor (TER 3)

`ter hook monitor` runs on PostToolUse and checks: bash commands that should
be Read, Grep or Glob; repeated reads of one file; runs of edits to one file;
identical tool calls; and repeated commands. When one trips, it returns
`additionalContext` (guidance for the agent) and a `systemMessage` (a notice
for you).

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash|Read|Edit|Write|Glob|Grep",
        "hooks": [
          {"type": "command", "command": "ter hook monitor", "timeout": 15}
        ]
      }
    ]
  }
}
```

Try it without Claude Code:

```bash
echo '{"session_id":"test","tool_name":"Bash","tool_input":{"command":"cat foo.py"}}' | ter hook monitor
ter hook monitor --help
```

Thresholds and the full output format are in
[docs/hooks-guide.md](../hooks-guide.md#customizing-thresholds).

### Capture and monitor together

Both can run on the same event; Claude Code runs every matching hook:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python -m ter hook"}]}
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {"type": "command", "command": "python -m ter hook"},
          {"type": "command", "command": "ter hook monitor", "timeout": 15}
        ]
      }
    ]
  }
}
```

The capture hook stays passive; only the monitor's guidance reaches the
agent. If you want analysis without influencing the session, register only
the capture hook.

## Hooks recommended by waste findings

The A3's countermeasures include hooks built from your session's findings.
Each is a settings snippet plus, usually, a small bash script using `jq`
and `sha1sum`. Install them only where the A3 found the waste, and measure
the next session.

| Detector that fired | Hook event (matcher) | Script | What it does |
|---|---|---|---|
| `repeated_exploration` | PreToolUse (`Read`) | `no-reread.sh` | Blocks re-reading a file whose contents have not changed since this session read the same range |
| `repeated_tool_call` | PreToolUse (`Bash`) | `no-repeat.sh` | Blocks an identical command while `git diff` and `git status` are unchanged since it last ran |
| `rework_cycle` | PostToolUse (`Bash`) | `same-failure.sh` | When a check fails with the same signature as last time, tells the agent to stop patching and re-diagnose |
| `unvalidated_implementation` | PostToolUse (`Edit\|Write`) | inline command | Runs the session's own test command after each edit and feeds a failure back |
| `regeneration` | PreToolUse (`Write`) | `edit-not-write.sh` | Blocks whole-file rewrites of files that exist, so changes go through Edit |

The other detectors recommend settings or CLAUDE.md lines instead:
`premature_implementation` suggests `"permissions": {"defaultMode": "plan"}`,
`unnecessary_handoff` suggests `"permissions": {"deny": ["Task"]}` for small
tasks, and `excessive_planning`, `fragmented_edits`, `unused_context` and
`repeated_reasoning` suggest CLAUDE.md lines and practices.

### Installing one

Take the snippet from the A3 page (section 5) or its JSON:

```bash
ter a3 session.jsonl --json a3.json
jq -r '.countermeasures[] | select(.detector == "regeneration") | .actions[] | select(.kind == "hook") | .snippet' a3.json
```

The snippet is the settings JSON, a blank line, then the script. For
`regeneration` that is:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write",
        "hooks": [
          {"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/edit-not-write.sh"}
        ]
      }
    ]
  }
}
```

```bash
#!/usr/bin/env bash
# .claude/hooks/edit-not-write.sh: PreToolUse(Write). Blocks whole-file rewrites
# of files that already exist, so changes go through Edit.
file=$(jq -r '.tool_input.file_path // empty')
if [ -n "$file" ] && [ -f "$file" ]; then
  echo "$file exists: change it with Edit instead of rewriting it." >&2
  exit 2
fi
```

1. Save the script as `.claude/hooks/edit-not-write.sh` and `chmod +x` it.
2. Merge the `hooks` entry into `.claude/settings.json`.
3. Start a new session and ask for a change to an existing file; the agent
   should use Edit.

Exit code 2 from a PreToolUse hook blocks the call and shows stderr to the
agent; from a PostToolUse hook it feeds stderr back after the call. Anything
else lets the session continue.

### Keeping hooks from becoming waste

- Start with the single most costly detector in the Pareto, not all of them.
- Compare the A3 follow-up metric on the next comparable session. If the
  waste did not fall, remove the hook.
- A hook that blocks often without improving flow efficiency is itself
  waiting and rework. Evidence-based, auditable interventions with cooldowns
  are the L4 roadmap (points P161 to P180).

## Troubleshooting

- **Nothing recorded.** Run Claude Code with `--verbose` (or read its debug
  log) and look for `ter hook: event not recorded:` lines on stderr. Check
  the settings file is valid JSON
  (`python -m json.tool .claude/settings.json`) and that the `python` in the
  command imports TER: `python -c "import ter"`.
- **Events in the wrong place.** The hook uses `--event-log`, else
  `TER_EVENT_LOG_DIR`, else `$XDG_CACHE_HOME/ter/events` (or
  `~/.cache/ter/events`). If the hook's environment
  differs from your shell's, pass `--event-log` in the hook command itself.
- **A countermeasure hook blocks legitimate work.** The scripts are examples
  to adapt. `no-repeat.sh`, for instance, keys on the working tree, so a
  command whose result depends on something outside git (a server, the
  network) should be excluded in the script.
