# Real session corpus

TER's detectors, thresholds and research claims need real sessions, not only
the synthetic golden corpus (issue #34). Real sessions hold secrets, personal
paths and other people's code, so they enter TER only through
`python -m ter corpus import`, which redacts every session before writing it.
Nothing raw is ever committed.

## Importing

Claude Code keeps transcripts in `~/.claude/projects/<project>/`, with
subagent transcripts in subfolders. Import a project folder, several, or all
of them:

```bash
python -m ter corpus import ~/.claude/projects --out ~/ter-data/corpus
python -m ter corpus import ~/.claude/projects/my-project --out ~/ter-data/corpus --labels labels.csv
```

Keep the corpus outside the repository (`ter-data/` and `*.raw.jsonl` are
ignored by git as a backstop). `--out` may not be inside a source folder.

The command writes:

| Path | What |
|---|---|
| `sessions/p-<hash>/<file>.jsonl` | The redacted session. The project folder name is a pseudonym; session and agent ids keep their names and any other file or folder name is hashed. |
| `reports/p-<hash>/<file>.json` | `ter.redaction-report/1`: every replacement by kind, record line and JSON path, never the value. |
| `manifest.json` | `ter.corpus-manifest/1`: the policy used and one entry per session (records, dates, coverage, unrecognised record types, redaction counts, labels, load error). |
| `.salt` | Private (mode 0600). Keeps pseudonyms stable when you import more sessions into the same corpus. Never share it. |

Importing into an existing corpus adds to it: sessions imported again are
replaced, the others stay listed. A corpus has one redaction policy, so
changing `--max-tool-output`, `--keep-tool` or `--quote-files` needs a new
`--out`. Two sources that would write the same file (the same project folder
and session copied to two places) are refused before anything is written.

It prints a summary and flags sessions below 99% coverage (TER-SRC-005: the
session source should account for almost every real record). It exits 1 when a
session could not be read or loaded (the rest are still imported), and 2 on
bad input such as an invalid label file.

## Coverage of real record types

Coverage is the share of a session's records the Claude Code session source
accounts for: `user` and `assistant` records mapped to events, plus record
types documented as metadata that carry no agent activity
(`METADATA_TYPES` in `ter.adapters.driven.claude_code.session_source`):
`agent-name`, `ai-title`, `artifact-autoreact-ledger`,
`artifact-comment-monitor`, `atis-latch`, `attachment`, `bridge-session`,
`cost-state`, `custom-title`, `file-history-delta`, `file-history-snapshot`,
`fork-context-ref`, `frame-link`, `last-prompt`, `mode`, `permission-mode`,
`pr-link`, `queue-operation`, `summary` and `system`. The manifest lists them per session
under `metadata_by_type`; anything else stays under `unrecognised_by_type`, so
a new Claude Code record type lowers coverage until it is classified.

Leigh's first import (286 sessions, 9 Oct 2026) put every session below 99%
because these types were unknown. One of them hid real intent: a prompt typed
while the agent works arrives as an `attachment` of type `queued_command`, not
as a `user` record. The session source now emits a typed queued prompt as
`intent.stated` in file order (TER-SRC-024); task notifications and messages
from other agents in the same queue stay metadata.

The second import (corpus-v2, same day, after that fix) left 33 of 286
sessions below 99%, all from six more types written by Remote Control and
artifact sessions: `bridge-session` (492 records), `frame-link` (318),
`fork-context-ref` (34), `artifact-autoreact-ledger` (25),
`artifact-comment-monitor` (23) and `agent-name` (12). None carries a prompt,
tool call or response, so they are metadata too.

The reference corpus for TER-SRC-005 is a content-free fingerprint,
`tests/fixtures/corpus/reference-record-types.json` (record counts by type,
attachment type and system subtype, sessions named by salted hash), written by
`scripts/corpus_record_types.py`. See `tests/fixtures/corpus/README.md`.

## What redaction does

The redactor (`ter.adapters.driven.claude_code.redaction`) is pure and runs
on each record before anything is written (TER-SRC-006):

- **Secrets** become `[REDACTED:<kind>#<tag>]`, where the tag is a short
  salted hash: the same secret gets the same tag, so calls that differ only in
  a secret stay different. Kinds: private keys, AWS access keys,
  GitHub and Slack tokens, `sk-` API keys, JWTs, bearer tokens, `password=` and
  similar assignments (the name is kept so code still reads), email addresses
  and IPv4 addresses (TER-SRC-020).
- **Paths**: each working directory becomes `/repo-<hash>`, its encoded
  project folder name `-repo-<hash>`, and each home directory `/home-<hash>`.
  The hash is salted and the same for the whole corpus, so a file read twice
  is still the same path (TER-SRC-021).
- **File contents** returned by `Read` become
  `[file content: N lines, sha256:...]`.
- **Other tool output** longer than `--max-tool-output` characters (default
  2000) becomes `[tool output dropped: N chars, sha256:...]`.
- **Images** and Claude Code's `toolUseResult` copies are dropped.

Record types, uuids, parent links, timestamps, message ids, tool names, tool
argument keys and usage blocks are never changed, so a redacted session yields
exactly the same `ter.event` ids, kinds and coverage as the original
(TER-SRC-022). Analyses and findings on the corpus therefore cite the same
evidence ids a local run would.

Options for code you may share:

```bash
python -m ter corpus import ~/.claude/projects/oss-lib --out ~/ter-data/oss --quote-files --keep-tool Bash --max-tool-output 8000
```

`--quote-files` keeps file contents (still scrubbed for secrets); use it only
for repositories whose licence allows redistribution. `--keep-tool NAME` keeps
that tool's output whatever its length.

### Known limits

- Patterns catch common secret shapes, not every secret. Read the reports and
  spot-check sessions before sharing anything.
- `Write` and `Edit` inputs are kept (scrubbed) because the rework and
  handoff detectors need them; they may contain proprietary code.
- Prompts are kept (scrubbed): they are the intent TER measures against.
- Names of people, companies and internal hosts are not detected.

## Labels

`--labels` reads a CSV joined to sessions by `session_id`:

```text
session_id,task_category,task,outcome,rating,licence
0b6f8c4e-2d1a-4f7e-9c3b-5a8d7e6f1a2b,bugfix,fix rounding in invoices,merged,4,proprietary
```

`outcome` is one of `merged`, `abandoned`, `partial`, `unknown`; `rating` is 1
to 5. Empty cells are left out, and free text is scrubbed like the sessions. The summary lists sessions with no labels and
labels that matched no session. Subagent transcripts share their parent's
session id, so they carry its labels.

## Before a corpus is published

Fill in a [dataset card](dataset-card.md). Points P087, P092, P095 and P181
stay open until a real corpus with recorded privacy and licence clearance
exists; the tooling alone never marks them done (the real-data rule).
