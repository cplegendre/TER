# Golden characterisation corpus

These snapshots freeze what TER computes today. The TER 4 rebuild moves code
into the `ter` package; if a move changes any number here, a test fails.

| Session | What it exercises |
|---|---|
| `example_session` | Shipped sample: login form, one duplicated read |
| `fixture_session` | Existing test fixture |
| `rework_loop` | Edit, test, fail cycles with a repeated failing command |
| `duplicate_exploration` | Re-reads, repeated search, restated reasoning, and a repetitive final reply that TER 3 does not flag |
| `intent_shift` | The developer changes the task mid-session and the agent adds unrequested functions; TER 3 does not flag the drift |
| `handoff_fetch` | Subagent handoff, web fetch, todo list, unrecognised record types |
| `lean_mix` | L2 detectors the others miss: excessive planning, a repeated shell call, a duplicated validation run, a whole-file rewrite of the agent's own file, fragmented edits, responding after a failing check |
| `iteration_converges` | Productive iteration: fail → fix → a different failure → fix → pass, which L2 must not call rework |

Gaps noted above are recorded on purpose: they are TER 3's baseline, and
the L2 detectors should close them with a reviewed snapshot change.

Each session is analysed twice (`default` and `fine` span segmentation), and
its normalised event stream is snapshotted as `<name>.events.json`. The visual
report layer is frozen as `<name>.report.json` (view-model),
`report/<name>.html` and, for the sample session, `report/example_session/*.svg`.
The L2 explanation is frozen as `<name>.lean.json` (findings, per-event
classification and basis, value stream, scorecard, evidence graph),
`<name>.a3.json` (the A3 view-model) and `report/<name>.a3.html`.

`corpus.py` lists these layouts (`SNAPSHOT_KINDS`, `REPORT_KINDS`,
`CHART_SESSIONS`), and `test_corpus_integrity.py` fails on a missing snapshot
or on any file under `snapshots/` or `snapshots/report/` that belongs to no
listed session.

Tokenizer and embedder are pinned to deterministic offline adapters (see
`docs/decisions/0002-hermetic-golden-characterisation.md`), so the tests need
no network or model weights.

## Changing a snapshot

A snapshot diff is a behaviour change. Regenerate deliberately and commit the
diff on its own so reviewers can see exactly which scores moved:

```bash
TER_UPDATE_GOLDEN=1 pytest tests/golden
git diff tests/golden/snapshots
```

To add a session, drop a `.jsonl` file in `sessions/` and regenerate.
