# GARE run c2ffdf8f1b09 (real, recorded)

**This is a real run, not a mock.** Every response came from a real model,
`qwen3-coder-next:latest`, served by a local Ollama on the recorder's PC. It is
the recorded run issue #55 asked for. No cloud provider keys exist on that PC,
so the run spent $0.

| Fact | Value |
|---|---|
| GARE | version 0.0.50, commit `8b4e2a4` |
| Run id | `c2ffdf8f1b09` |
| Command | `gare mission --execute --max-repairs 1` |
| Repository | a throwaway toy repository: an off-by-one bug in `toy/stats.py` `last_n`, one failing pytest |
| Provider | local Ollama, model `qwen3-coder-next:latest` (provider `ollama`) |
| Routing | a deliberately unavailable route, `ollama_down` (`qwen3-coder-next:notpulled`), was ranked first in three route decisions; GARE skipped it and never called it |
| Repair | attempt 1 failed; one diagnosis (`ready`, confidence 0.95); attempt 2 |
| Review | the reviewer persona said `revise` |
| Outcome | final state `needs_review`, score 20/100, test exit code 1 |
| Usage | 4 model calls, 2,814 input and 1,526 output tokens |

Exported with GARE's own commands:

```text
gare export-ter --run c2ffdf8f1b09 -o gare-ter-usage.jsonl
gare explain c2ffdf8f1b09 --json > explain.json
```

The only edit to the exported files is that local paths were replaced with
`/sandbox` before they left the PC. Their content is otherwise as exported.

## What this run proves

The `gare-run` session source reads a real GARE run into a `ter.event`
stream that passes the session source contract suite (TER-SRC-010), with token
totals equal to the export (TER-SRC-011), every run event state known
(coverage 1.0, TER-SRC-012), and the mission's repair attempts, reviewer
verdict and `needs_review` outcome mapped.

Reading it showed one adapter bug the mock runs could not: a `route.selected`
event named the top-ranked candidate (`ollama_down`) even though GARE never
called it. It now names the highest-ranked candidate the run called, and lists
the ones ranked above it as not called.

## What it does NOT cover

- **No failover.** `ollama_down` was skipped before any call, so the run has
  no failed call and no `route.failover` event. Failover is still covered only
  by the mock `failover-run/` fixture.
- **No cloud provider.** Only a local Ollama model ran; no priced model, so
  nothing here checks cost from the price book.
- **No latency.** `latency_ms` is `null` in every usage row (and
  `avg_latency_ms` in `explain.json`); TER reads no latency from GARE.
- **No repair fingerprint use.** The `diagnosis` run event carries a
  fingerprint, but TER maps no event for diagnoses yet.
