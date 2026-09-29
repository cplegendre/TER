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

Gaps noted above are recorded on purpose: they are TER 3's baseline, and
the L2 detectors should close them with a reviewed snapshot change.

Each session is analysed twice (`default` and `fine` span segmentation), and
its normalised event stream is snapshotted as `<name>.events.json`.

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
