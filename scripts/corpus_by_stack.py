"""Compare token use and waste across languages and stacks, stratified (L6).

Explains every session (TER off) and groups sessions by dominant language and
by repository stack, each crossed with the ``task_category`` and ``outcome``
labels (TER-STK-010). Every stratum reports its session count; a stratum with
fewer than ``--min-sessions`` sessions (default 5) is marked insufficient and
reports no measure (TER-STK-011). Each median comes with its 25th and 75th
percentiles, and each waste rate with the share of sessions above zero
(TER-STK-012); sessions that touch no file form the language group
``none (no files)``, which is never compared (TER-STK-013). The output holds
counts, medians, percentiles and ratios only: no prompt, path, code or
repository name, so it can be shared from a private corpus.

Two inputs:

- an imported corpus (``python -m ter corpus import ... --labels``), whose
  manifest carries the labels; redacted paths keep their extensions, so
  languages are known, but there is no repository, so the stack is
  ``unknown``::

    python scripts/corpus_by_stack.py corpus ~/ter-data/corpus --out by-stack.json

- raw transcripts and a labels file (the D4 file: ``session_id``,
  ``task_category``, ``outcome`` and, for the stack, ``cwd`` and ``commit``).
  With ``--work``, each session's repository is exported at its start commit
  with ``git archive`` (read-only for the repository, as in
  ``scripts/corpus_grounded.py``) and its manifests give the stack::

    python scripts/corpus_by_stack.py raw labels-d4.csv ~/.claude/projects \
        --work ~/ter-data/grounded --out by-stack.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_grounded import export  # noqa: E402

from ter.application.compare_stacks import (  # noqa: E402
    CompareByStack,
    CorpusSession,
)
from ter.application.explain import ExplainedSession  # noqa: E402
from ter.domain.stack_comparison import DEFAULT_MIN_SESSIONS  # noqa: E402

LABELS = ("task_category", "outcome")


def corpus_sessions(corpus: Path) -> Iterator[CorpusSession]:
    """Every session of an imported corpus, with its manifest labels."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest.get("sessions", []):
        if not isinstance(entry, dict) or entry.get("load_error"):
            continue
        labels = entry.get("labels") or {}
        yield CorpusSession(
            corpus / str(entry["file"]),
            {k: str(labels[k]) for k in LABELS if labels.get(k)},
        )


def raw_sessions(
    labels: Path, projects: Path, work: Path | None
) -> Iterator[CorpusSession | str]:
    """Every labelled session with a transcript; a string names a failure."""
    transcripts = {p.stem: p for p in projects.expanduser().glob("*/*.jsonl")}
    with labels.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        sid = (row.get("session_id") or "").strip()
        path = transcripts.get(sid)
        if path is None:
            yield "no transcript"
            continue
        repo: Path | None = None
        if work is not None and row.get("cwd") and row.get("commit"):
            repo = work.expanduser() / "repos" / sid
            try:
                export(row["cwd"], row["commit"], repo)
            except Exception:  # noqa: BLE001 - the session still has languages
                repo = None
        yield CorpusSession(
            path,
            {k: row[k].strip() for k in LABELS if (row.get(k) or "").strip()},
            repo,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    corpus = sub.add_parser("corpus", help="an imported corpus directory")
    corpus.add_argument("corpus", type=Path)
    raw = sub.add_parser("raw", help="raw transcripts and a labels CSV")
    raw.add_argument("labels", type=Path)
    raw.add_argument("projects", type=Path, help="~/.claude/projects")
    raw.add_argument("--work", type=Path, help="export repositories here")
    for p in (corpus, raw):
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    args = parser.parse_args(argv)

    from ter.bootstrap import cli_services

    explain_transcript = cli_services().explain_transcript
    assert explain_transcript is not None

    def explain(path: Path, repo: Path | None) -> ExplainedSession:
        if repo is None:
            return explain_transcript(path, "regex", "off")
        return explain_transcript(path, "regex", "off", repo=repo)

    missing = 0
    if args.mode == "corpus":
        sessions = list(corpus_sessions(args.corpus.expanduser()))
    else:
        sessions = []
        for item in raw_sessions(args.labels, args.projects, args.work):
            if isinstance(item, str):
                missing += 1
            else:
                sessions.append(item)
    report = CompareByStack(explain, args.min_sessions)(sessions)
    document = report.as_dict()
    if missing:
        document["errors"] = {**report.errors, "no transcript": missing}
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: document[k] for k in ("schema", "sessions", "analysed", "errors")},
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
