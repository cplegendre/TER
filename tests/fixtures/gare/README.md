# GARE run fixtures

`runs/` holds **real** recorded runs; the two folders beside it are mock runs.

## Real runs

| Folder | Run | What it shows |
|---|---|---|
| `runs/c2ffdf8f1b09/` | GARE 0.0.50 `gare mission --execute --max-repairs 1` with a local Ollama model (issue #55) | A real repair mission: two coder attempts, a diagnosis, a reviewer `revise` verdict and a `needs_review` outcome; an unavailable route ranked first and never called. No failover, no cloud provider, no latency (see its README). |

The real run passes the session source contract suite, which verifies
TER-SRC-010. Failover is still covered by mock data only.

## Mock runs

Two runs exported from GARE v0.46 (commit 206a97e) with its own commands, then
with sandbox paths replaced by `/sandbox`:

```text
gare export-ter --run RUN -o gare-ter-usage.jsonl
gare explain RUN --json > explain.json
```

**These are mock runs, not real ones.** Every response comes from GARE's
`mock` provider. Under the real-data rule they exercise the GARE adapter but
prove nothing about real runs on their own; `failover-run/` is still the only
failover TER has seen.

| Folder | Run | What it shows |
|---|---|---|
| `failover-run/` | Orchestrator `software` workflow, 9 tasks | A provider (`flaky/flaky-large`, a local stub answering 503) is ranked first and fails three times before its circuit opens; each failure fails over to `mock/mock-smart`. |
| `repair-mission/` | `gare mission --execute --max-repairs 1` on a two-file repository | One coder attempt, an investigation, a diagnosis with too little evidence to repair, and a final `blocked` state with outcome score 5/100. |

Neither run reaches a verification step: GARE's mock provider writes no
patch, so no tests run. Unit tests cover verification events with inline
run events instead.
