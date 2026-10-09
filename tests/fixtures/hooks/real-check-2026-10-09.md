# Real hooks check, 9 October 2026

A content-free summary of three runs of `python -m ter hooks check` by the
repository owner on hook payloads recorded with `ter hook --record` in real
work. It holds counts and the check's fixed reason strings only: no prompt,
tool input, tool output, path or event id. The recordings and transcripts
stay on the owner's machine (issue #35).

| | |
|---|---|
| Agent | Claude Code |
| Platform | Windows |
| Recorded | 9 October 2026 |
| Sessions | 2 real sessions; the demo session (the synthetic payloads in this folder) excluded |

## First run

TER version checked: `7757b42`, before the session source derived
`subagent.completed`.

### Hook events against transcript event ids (TER-OBS-007)

87 of 94 hook events (92.6%) had an event with the same id in the stream the
session source reads from the transcript.

| Kind | Matched | Unmatched | Reason for the misses |
|---|---:|---:|---|
| `intent.stated` | 11 | 0 | |
| `tool.requested` | 34 | 0 | |
| `tool.completed` | 32 | 0 | |
| `task.completed` | 10 | 0 | |
| `subagent.completed` | 0 | 7 | the session source derives no events of this kind |
| **Total** | **87** | **7** | |

### Stop payloads against the transcript stop (TER-OBS-005)

10 of 10 real Stop payloads gave a `task.completed` id equal to the id the
session source derives for the same stop.

## Second run

TER version checked: after the session source derived `subagent.completed`
from subagent transcripts, before the hooks check told internal helper
agents apart. More work had been recorded in both sessions since the first
run.

### Hook events against transcript event ids (TER-OBS-007)

Every prompt, tool and stop event matched. Per session (the larger session
is a Remote Control session):

| Kind | Remote Control session | Other session | Matched | Unmatched |
|---|---:|---:|---:|---:|
| `intent.stated` | 9 | 3 | 12 | 0 |
| `tool.requested` | 31 | 6 | 37 | 0 |
| `tool.completed` | 29 | 6 | 35 | 0 |
| `task.completed` | 8 | 3 | 11 | 0 |

95 of 95 prompt, tool and stop events (100.0%) matched.

The 9 SubagentStop payloads (7 in the Remote Control session, 2 in the
other) all missed with "no subagent transcript for the hook's agent_id under
<session>/subagents". On the owner's machine, read-only:

- every one has an empty `agent_type`;
- every one names an `agent_transcript_path`
  (`<session>/subagents/agent-<agent_id>.jsonl`), and none of the 9 files
  exists; no file or folder under `~/.claude` holds any of the 9 agent ids;
- the Remote Control session started no Agent-tool subagent and has no
  `subagents` folder, yet sent 7 of them; the other session's 29 subagent
  files all predate the hooks and match none of the 9 ids.

So these are Claude Code's own internal helper agents, not Agent-tool
subagents: no transcript is ever written for them, so there is nothing to
match. The hook adapter now records no event for such a stop and the hooks
check reports it apart ("no transcript by design", outside the TER-OBS-007
denominator). Counted by that rule (not yet re-run), the second run reads
95 of 95 hook events matched, with 9 internal helper SubagentStops reported
apart.

### Stop payloads against the transcript stop (TER-OBS-005)

11 of 11 real Stop payloads gave a `task.completed` id equal to the id the
session source derives for the same stop.

## Third run

TER version checked: `cdd2cb9` (the session source derives
`subagent.completed`; the check did not yet tell internal helper agents
apart, nor tool calls not yet written). Run from inside the Remote Control
session, while it was still going. This time that session spawned one
Agent-tool subagent (`agent_type` `general-purpose`, its transcript file
present).

### Hook events against transcript event ids (TER-OBS-007)

| Kind | Remote Control session | Other session | Matched | Unmatched |
|---|---:|---:|---:|---:|
| `intent.stated` | 13 | 3 | 16 | 0 |
| `tool.requested` | 39 | 6 | 45 | 1 |
| `tool.completed` | 36 | 6 | 42 | 1 |
| `task.completed` | 12 | 3 | 15 | 0 |
| `subagent.completed` | 1 | 0 | 1 | 10 |
| **Total** | | | **119** | **12** |

- The Agent-tool subagent's SubagentStop gave the `subagent.completed` id the
  session source derives from its transcript: the first real subagent
  matched.
- The 10 other SubagentStops (8 in the Remote Control session, 2 in the
  other) all had an empty `agent_type` and no transcript file: internal
  helper agents, which the hook adapter now records no event for and the
  check reports apart.
- The 1 unmatched tool request and its completion had the reason "no
  transcript event for the same record". Most likely they are the tool call
  that was running the check: its hooks had fired, but its records were not
  in the transcript yet when the check read it. The check now reports a hook
  event received after the transcript's last record as "not yet written to
  the transcript", outside the match.

Counted by the current rules (not yet re-run), the third run reads
119 of 121 hook events matched, with 10 internal helper SubagentStops
reported apart, and the 2 remaining events most likely not yet written.

### Stop payloads against the transcript stop (TER-OBS-005)

15 of 15 real Stop payloads gave a `task.completed` id equal to the id the
session source derives for the same stop.

## What it proves

- **TER-OBS-005** holds on real data, on all three runs: verified.
- **TER-OBS-007** holds on real data: on every run, every prompt, tool
  request, tool completion and stop whose record was in the transcript got
  the transcript's id, and in the third run so did the one Agent-tool
  subagent: verified.
- **TER-OBS-013**: the transcript-less SubagentStops (9 in the second run,
  10 in the third) are Claude Code internal helper agents (empty
  `agent_type`, transcript file never written).
