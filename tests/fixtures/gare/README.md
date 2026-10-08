# GARE run fixtures (mock)

Two runs exported from GARE v0.46 (commit 206a97e) with its own commands, then
with sandbox paths replaced by `/sandbox`:

```text
gare export-ter --run RUN -o gare-ter-usage.jsonl
gare explain RUN --json > explain.json
```

**These are mock runs, not real ones.** Every response comes from GARE's
`mock` provider. Under the real-data rule they exercise the GARE adapter but
never mark P103, P105 or TER-SRC-010 done; that waits on a real recorded run
(issue #55).

| Folder | Run | What it shows |
|---|---|---|
| `failover-run/` | Orchestrator `software` workflow, 9 tasks | A provider (`flaky/flaky-large`, a local stub answering 503) is ranked first and fails three times before its circuit opens; each failure fails over to `mock/mock-smart`. |
| `repair-mission/` | `gare mission --execute --max-repairs 1` on a two-file repository | One coder attempt, an investigation, a diagnosis with too little evidence to repair, and a final `blocked` state with outcome score 5/100. |

Neither run reaches a verification step: GARE's mock provider writes no
patch, so no tests run. Unit tests cover verification events with inline
run events instead.
