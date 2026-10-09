# Reference corpus record types (TER-SRC-005)

`reference-record-types.json` (`ter.corpus-record-types/1`) is the content-free
fingerprint of the reference corpus: for each real Claude Code session, the
number of records of each `type`, each `attachment.type` and each `system`
subtype. Session names are salted hashes; no prompt, path or code is kept.

It is written by `scripts/corpus_record_types.py` on the machine that holds the
sessions, and `tests/unit/test_ter4_corpus_coverage.py` checks that the Claude
Code session source accounts for at least 99% of every session's records
(mapped to events, or a documented metadata type).

| Label | Source |
|---|---|
| `ter-cloud` | Claude Code 2.1.295 sessions of the TER project's own cloud sessions (a lead session and six subagents), 9 Oct 2026 |
| `leigh-pc` | Leigh's local `~/.claude/projects` (Windows), imported into the issue #34 corpus, 9 Oct 2026 |

Refresh it after a Claude Code upgrade:

```bash
python scripts/corpus_record_types.py ~/.claude/projects --label leigh-pc \
    --merge tests/fixtures/corpus/reference-record-types.json \
    --out tests/fixtures/corpus/reference-record-types.json
```
