"""Run the L3 grounded analysis over real sessions at their start commits.

Reads a labels file with ``session_id``, ``cwd`` and ``commit`` columns (the
D4 file), exports each session's repository at its start commit with
``git archive`` (read-only for the repository) and runs ``ter explain --repo``
on the session's raw transcript. Writes two files:

- ``--out``: counts only (change-surface placements and grounded findings per
  detector, per repository), safe to share from a private corpus;
- ``--review``: one row per grounded finding with the edited path and the
  task's seed files, for the owner to judge true or false on their own
  machine. It holds repository paths, so it is not for sharing.

    python scripts/corpus_grounded.py labels-d4.csv ~/.claude/projects \
        --work ~/ter-data/grounded --out grounded.json --review review.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import tarfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "ter.corpus-grounded/1"
DETECTORS = ("unrelated_modification", "surface_expansion", "boundary_violation")


def export(cwd: str, commit: str, dest: Path) -> None:
    """The repository at ``commit``, unpacked into ``dest`` (once)."""
    if (dest / ".ter-exported").exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(
        ["git", "-C", cwd, "archive", "--format=tar", commit],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    (dest / ".ter-exported").write_text(commit, encoding="utf-8")


def main() -> None:
    from ter.bootstrap import cli_services

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("projects", type=Path, help="~/.claude/projects")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()

    explain = cli_services().explain_transcript
    projects = args.projects.expanduser()
    transcripts = {p.stem: p for p in projects.glob("*/*.jsonl")}
    with args.labels.open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("commit")]

    placements: dict[str, Counter[str]] = defaultdict(Counter)
    confident: dict[str, Counter[str]] = defaultdict(Counter)
    uncertain: dict[str, Counter[str]] = defaultdict(Counter)
    errors: Counter[str] = Counter()
    sessions: Counter[str] = Counter()
    review: list[dict[str, Any]] = []
    started = time.monotonic()
    for row in rows:
        sid, repo = row["session_id"], row.get("repo") or "?"
        path = transcripts.get(sid)
        if path is None:
            errors["no transcript"] += 1
            continue
        tree = args.work.expanduser() / "repos" / f"{sid}"
        try:
            export(row["cwd"], row["commit"], tree)
            explained = explain(path, "regex", "off", repo=tree)
        except Exception as exc:  # noqa: BLE001 - a failure is a result here
            errors[type(exc).__name__] += 1
            continue
        sessions[repo] += 1
        analysis = explained.analysis
        seeds: dict[str, str] = {}
        for surface in analysis.surfaces:
            for edit in surface.edits:
                placements[repo][edit.placement.value] += 1
                seeds[edit.path] = " ".join(surface.seeds)
        for f in analysis.findings:
            if f.detector not in DETECTORS:
                continue
            (uncertain if f.uncertain else confident)[repo][f.detector] += 1
            review.append(
                {
                    "session_id": sid,
                    "repo": repo,
                    "detector": f.detector,
                    "confidence": round(f.confidence, 2),
                    "subject": f.subject,
                    "seeds": seeds.get(f.subject, ""),
                    "verdict": "",
                }
            )

    out = {
        "schema": SCHEMA,
        "sessions": sum(sessions.values()),
        "errors": dict(errors),
        "seconds": round(time.monotonic() - started, 1),
        "repos": {
            repo: {
                "sessions": n,
                "placements": dict(placements[repo]),
                "confident": dict(confident[repo]),
                "uncertain": dict(uncertain[repo]),
            }
            for repo, n in sorted(sessions.items())
        },
    }
    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    with args.review.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "session_id",
                "repo",
                "detector",
                "confidence",
                "subject",
                "seeds",
                "verdict",
            ],
        )
        writer.writeheader()
        writer.writerows(review)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
