# Real hooks check, 9 October 2026

A content-free summary of `python -m ter hooks check` run by the repository
owner on hook payloads recorded with `ter hook --record` in real work. It
holds counts and the check's fixed reason strings only: no prompt, tool
input, tool output, path or event id. The recordings and transcripts stay on
the owner's machine (issue #35).

| | |
|---|---|
| Agent | Claude Code |
| Platform | Windows |
| Recorded | 9 October 2026 |
| Sessions | 2 real sessions; the demo session (the synthetic payloads in this folder) excluded |
| TER version checked | `7757b42`, before the session source derived `subagent.completed` |

## Hook events against transcript event ids (TER-OBS-007)

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

## Stop payloads against the transcript stop (TER-OBS-005)

10 of 10 real Stop payloads gave a `task.completed` id equal to the id the
session source derives for the same stop.

## What it proves

- **TER-OBS-005** holds on real data: verified.
- **TER-OBS-007** holds for every kind but `subagent.completed`, which the
  session source did not derive at the time. Since then it derives one per
  finished subagent from `<session>/subagents/agent-<agent_id>.jsonl`, keyed
  as the SubagentStop hook keys it. OBS-007 stays `planned` until one re-run
  of the hooks check on sessions with subagents reports them matched.
