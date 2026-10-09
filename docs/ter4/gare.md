# GARE runs

[GARE](https://github.com/lgriffin/GARE) (General Agent Routing & Execution)
routes each task of a run to a provider and model, fails over when a route
fails and, in mission mode, repairs and verifies patches. TER reads a GARE run
as a session through the `gare-run` session source
(`ter.adapters.driven.gare`, issue #52). It reads only what GARE's own commands
export: it never opens GARE's database or imports the `gare` package, so no
extra install is needed.

## Exporting a run

In GARE's environment:

```text
gare export-ter --run RUN -o run/gare-ter-usage.jsonl
gare explain RUN --json > run/explain.json
```

Then in TER:

```bash
python -m ter observe run --timeline
python -m ter explain run
```

A reference is a folder holding either or both files, or one file. With only
the usage export, TER sees model calls and failovers; with only `explain.json`,
it sees routes, attempts and outcomes and takes each task's final route as its
model call. Use both.

A `.jsonl` file is a usage export when any of its rows has the
`gare.ter.usage.v2` schema, so a leading row of another schema or broken JSON
does not hide the export (it counts against coverage instead). An
`explain.json` that is not valid `gare explain --json` output, or names no run
id, is refused rather than read as a usage-only run. Usage rows of another run
count against coverage as `other-run:<id>`.

## Mapping

| GARE | `ter.event` kind | Actor |
|---|---|---|
| run event `created` (the goal) | `intent.stated` | user |
| `route_decision` (the highest-ranked candidate the run called) | `route.selected` | system |
| `task_started`, or the first route of mission coder attempt *n* | `attempt.started` | assistant |
| `gare.ter.usage.v2` row with tokens | `response`, with its tokens | assistant |
| `gare.ter.usage.v2` row with no tokens and no success | `route.failover`, with the error code from `explain.json` | system |
| `acceptance_evaluated`, `attempt_complete`, `runtime_qa`, `browser_qa`, `persona_review` | `verification.completed` (`pass`, `fail` or `unknown`) | system |
| final state (`succeeded`, `partial`, `failed`, `needs_review`, `blocked`) with a score | `outcome.recorded` (`blocked: score 5/100`) | system |
| final state | `task.completed` | system |

The routing kinds are lifecycle kinds: they are counted and never scored.
Only `route.failover` is a Lean step (a failed model call the run waited on,
classified by the `failed_route` detector as *waiting*, TER-DET-008); the
others add none. Only `response` events are generated work.

- GARE ranks a decision's candidates before it checks them, and skips an
  unavailable provider without calling it. So `route.selected` names the
  highest-ranked candidate the run called for that task (from the usage rows,
  the recorded errors and the task's final route), and lists any candidate
  ranked above it as not called:
  `mission-coder-1-…: ollama/qwen3-coder-next:latest (rank 2 of 2; not called:
  ollama_down/qwen3-coder-next:notpulled)`. A skipped route made no call, so it
  is not a failover. When the run called none of the candidates, the top one
  stands.
- Run event states TER knows but does not model (`planned`,
  `execution_mode`, `worktree_ready`, `diagnosis`, `repair_stopped` and
  similar) produce no event.
- An unknown state, or a row of an unknown schema, counts against the trace's
  coverage (TER-SRC-012).
- Event ids derive from the run id, file, row or event position and kind, so
  re-reading a run gives the same ids.
- Events are ordered by time. A row or run event with no valid timestamp keeps
  its place in its own file: it sorts at the time of the dated entry before it,
  or at the run's start.

## Limits

- **No response text.** The usage export carries counts, not what the model
  wrote, so a `response` event's text is its task and route. Measures counted
  from event text (the observe report's text tokens, Lean's generated tokens
  and flow efficiency) therefore measure those labels; the `input` and
  `output` usage figures are GARE's own counts. Every GARE trace carries the
  `no-response-text` usage limit, and `observe`, `explain` and the A3 (JSON
  and HTML) state it (TER-SRC-016).
- **No TER score.** TER 3 scoring reads Claude Code JSONL only, so `explain`
  and `a3` report a GARE run without a TER score and say so on stderr
  (TER-SRC-017).
- **No cache tokens.** GARE records input and output tokens only. For
  OpenAI-style and Gemini providers its input count already includes cached
  tokens, with no split. Every GARE trace carries the `no-cache-tokens` usage
  limit, and the observe report says so beside its usage figures
  (TER-SRC-013, TER-SRC-014).
- **Estimated tokens are not marked.** When a provider reports no usage, GARE
  stores a `len(text) / 4` estimate without saying so.
- **No row ids or attempt numbers in the usage export.** TER keys rows by
  their line in the file, and mission attempts by the attempt number in the
  task id (`mission-coder-<n>-…`).
- **No latency.** The usage export has a `latency_ms` field, but the real run
  recorded so far leaves it `null`; TER reads no latency from GARE.
- **Diagnoses map to no event.** The `diagnosis` run event carries a status,
  a confidence and a fingerprint of the repair hypothesis, but `ter.event`
  has no kind for a hypothesis yet, so TER leaves it out and the repair-loop
  detector (capability 7) does not see GARE repairs.
- **Gate receipts** (`gare.gate-receipt.v1`) are CI release receipts with no
  link to a run, so TER does not read them as run outcomes.
- **Prices.** Mock and local models have no price book entry, and cost from
  the price book is not attached to GARE runs yet (TER-SRC-015, planned).

## Fixtures

`tests/fixtures/gare/` holds (see its README):

- **One real recorded run** (issue #55), `runs/c2ffdf8f1b09/`: GARE 0.0.50
  `gare mission --execute --max-repairs 1` on a toy repository with an
  off-by-one bug, served by a local Ollama model (`qwen3-coder-next:latest`,
  $0 spent). Two coder attempts with an investigation and its diagnosis
  between them, a reviewer
  `revise` verdict, and a final `needs_review` state (score 20/100, tests
  still failing); 2,814 input and 1,526 output tokens over 4 calls. A
  deliberately unavailable route, `ollama_down`, was ranked first and never
  called. It passes the session source contract suite, which verifies
  TER-SRC-010 and the real-data part of P103. Reading it also found the
  ranked-but-never-called route that `route.selected` used to name.
- **Two mock runs** exported from GARE v0.46: a failover run and a repair
  mission. Every response comes from GARE's mock provider.

What the real run does **not** prove:

- **Failover.** Its unavailable route was skipped before any call, so it has
  no `route.failover`. Failover (and the `failed_route` detector on GARE data)
  is still proven on the mock `failover-run/` only.
- **Cloud providers and prices.** Only a local model ran.
- **Latency.** `latency_ms` is `null` throughout.
