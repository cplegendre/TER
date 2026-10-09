"""Count Claude Code record types per session, with no content (TER-SRC-005).

Writes the reference-corpus fixture ``tests/fixtures/corpus/reference-record-types.json``:
for every ``.jsonl`` transcript under the given folders, the number of records
of each ``type``, of each ``attachment.type`` and of each ``system`` subtype.
Files are named by a salted hash of their path, so nothing about the
developer's projects, prompts or code leaves the machine.

    python scripts/corpus_record_types.py ~/.claude/projects --label leigh-pc \
        --out reference-record-types.json [--merge existing.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "ter.corpus-record-types/1"


def count(path: Path) -> dict[str, Any]:
    types: Counter[str] = Counter()
    attachments: Counter[str] = Counter()
    systems: Counter[str] = Counter()
    unparsed = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                unparsed += 1
                continue
            if not isinstance(record, dict):
                types[type(record).__name__] += 1
                continue
            kind = str(record.get("type") or "<missing>")
            types[kind] += 1
            if kind == "attachment" and isinstance(record.get("attachment"), dict):
                attachments[str(record["attachment"].get("type") or "<missing>")] += 1
            if kind == "system":
                systems[str(record.get("subtype") or "<missing>")] += 1
    return {
        "records": sum(types.values()),
        "unparsed_lines": unparsed,
        "types": dict(sorted(types.items())),
        "attachment_types": dict(sorted(attachments.items())),
        "system_subtypes": dict(sorted(systems.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--label", required=True, help="where the sessions came from")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--merge", type=Path, help="an earlier file to add to")
    args = parser.parse_args()

    salt = secrets.token_hex(16)
    sessions: list[dict[str, Any]] = []
    for folder in args.folders:
        for path in sorted(folder.expanduser().rglob("*.jsonl")):
            digest = hashlib.sha256((salt + str(path)).encode()).hexdigest()[:16]
            entry = {
                "session": f"{args.label}-{digest}",
                "subagent": "subagents" in path.parts,
            }
            entry.update(count(path))
            sessions.append(entry)
    previous: list[dict[str, Any]] = []
    if args.merge is not None:
        previous = [
            s
            for s in json.loads(args.merge.read_text(encoding="utf-8"))["sessions"]
            if not str(s["session"]).startswith(f"{args.label}-")
        ]
    document = {"schema": SCHEMA, "sessions": previous + sessions}
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(f"{len(sessions)} sessions from {args.label} written to {args.out}")


if __name__ == "__main__":
    main()
