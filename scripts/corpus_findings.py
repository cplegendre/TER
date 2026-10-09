"""Run the L2 analysis over a corpus and summarise it, with no content.

For every session in an imported corpus (``python -m ter corpus import``), run
the same explanation ``ter a3`` runs (TER off) and count findings per
detector, confident and uncertain, with coverage, intent changes, peak WIP and
flow efficiency. The output holds counts and ratios only: no prompt, path,
code or finding text, so it can be shared from a private corpus.

    python scripts/corpus_findings.py ~/ter-data/corpus --out findings.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "ter.corpus-findings/1"


def main() -> None:
    from ter.bootstrap import cli_services

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    explain = cli_services().explain_transcript
    sessions = sorted((args.corpus.expanduser() / "sessions").rglob("*.jsonl"))
    confident: Counter[str] = Counter()
    uncertain: Counter[str] = Counter()
    with_finding: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for path in sessions:
        try:
            explained = explain(path, "regex", "off")
        except Exception as exc:  # noqa: BLE001 - a failure is a finding here
            errors[type(exc).__name__] += 1
            continue
        a = explained.analysis
        seen: set[str] = set()
        for f in a.findings:
            (uncertain if f.uncertain else confident)[f.detector] += 1
            seen.add(f.detector)
        with_finding.update(seen)
        card = a.scorecard
        rows.append(
            {
                "events": a.events,
                "coverage": round(explained.trace.coverage, 4),
                "unrecognised": sum(explained.trace.unrecognised_by_type.values()),
                "prompts": sum(
                    1 for e in explained.trace.events if e.kind.value == "intent.stated"
                ),
                "intent_changes": len(a.intent.changes),
                "low_alignment_periods": len(a.intent.periods),
                "peak_wip": a.wip.peak.total if a.wip.peak is not None else 0,
                "flow_efficiency_tokens": card.flow_efficiency_tokens,
            }
        )
    coverages = [r["coverage"] for r in rows]
    flows = [
        r["flow_efficiency_tokens"]
        for r in rows
        if r["flow_efficiency_tokens"] is not None
    ]
    document = {
        "schema": SCHEMA,
        "sessions": len(sessions),
        "analysed": len(rows),
        "errors": dict(errors),
        "seconds": round(time.monotonic() - started, 1),
        "coverage": {
            "min": min(coverages, default=None),
            "median": statistics.median(coverages) if coverages else None,
            "below_99": sum(1 for c in coverages if c < 0.99),
        },
        "flow_efficiency_tokens_median": statistics.median(flows) if flows else None,
        "detectors": {
            d: {
                "confident": confident[d],
                "uncertain": uncertain[d],
                "sessions_with_finding": with_finding[d],
            }
            for d in sorted(set(confident) | set(uncertain))
        },
        "per_session": rows,
    }
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps({k: v for k, v in document.items() if k != "per_session"}, indent=1)
    )


if __name__ == "__main__":
    main()
