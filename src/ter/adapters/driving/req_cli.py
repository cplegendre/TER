"""``ter-req``: lint, trace and report on the EARS requirement catalogue.

Usage::

    ter-req lint   [--catalogue requirements] [--tests tests]
    ter-req trace  --results req-trace.json [--gate L0] [--summary PATH]
    ter-req points [--catalogue requirements]
    ter-req report [--results req-trace.json] [--gate L0] [--out PATH]

``lint`` checks EARS grammar and, with ``--tests``, that every
``@pytest.mark.req`` in the test tree cites a known id (a static backward
trace). ``trace`` is the CI gate: it fails when a verified requirement at or
below the gate level has no passing test, or a test cites an unknown id.
Exit status is 0 on success, 1 when a check fails and 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from ter.adapters.driven.requirements_yaml import (
    Catalogue,
    CatalogueError,
    load_catalogue,
)
from ter.domain.maturity import Maturity
from ter.domain.requirements import (
    VISION_POINTS,
    TestOutcome,
    backward_trace,
    lint_catalogue,
    trace,
)

from .req_report import point_grid, render_markdown

DEFAULT_CATALOGUE = Path("requirements")
MARKER = re.compile(r"mark\.req\(\s*((?:[\"'][^\"']*[\"']\s*,?\s*)+)\)")
MARKER_ID = re.compile(r"[\"']([^\"']*)[\"']")

Results = dict[str, list[TestOutcome]]


class InputError(ValueError):
    """A results file is missing or malformed."""


def scan_citations(root: Path) -> dict[str, list[str]]:
    """Find ``mark.req("...")`` citations in Python files under ``root``.

    Returns id -> ["path:line", ...].
    """
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in MARKER.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            for rid in MARKER_ID.findall(match.group(1)):
                found.setdefault(rid, []).append(f"{path.as_posix()}:{line}")
    return found


def load_results(path: Path) -> Results:
    """Read the JSON written by ``pytest --req-trace``."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: cannot read trace results: {exc}") from exc
    mapping = document.get("requirements") if isinstance(document, dict) else None
    if not isinstance(mapping, dict):
        raise InputError(f"{path}: expected a 'requirements' mapping")
    results: Results = {}
    for rid, entries in mapping.items():
        if not isinstance(entries, list):
            raise InputError(f"{path}: {rid} must map to a list")
        outcomes = []
        for entry in entries:
            if not isinstance(entry, dict) or not {"nodeid", "outcome"} <= set(entry):
                raise InputError(f"{path}: {rid} has an entry without nodeid/outcome")
            outcomes.append(TestOutcome(str(entry["nodeid"]), str(entry["outcome"])))
        results[str(rid)] = outcomes
    return results


def _cmd_lint(args: argparse.Namespace, catalogue: Catalogue) -> int:
    issues = lint_catalogue(catalogue.requirements, catalogue.vocabulary)
    for issue in issues:
        source = catalogue.sources.get(issue.requirement_id)
        prefix = f"{source}: " if source else ""
        print(f"{prefix}{issue}")
    failed = bool(issues)
    if args.tests is not None:
        unknown = backward_trace(catalogue.requirements, scan_citations(args.tests))
        for rid, where in unknown:
            print(
                f"{rid}: [TRACE-UNKNOWN] cited but not in the catalogue: {', '.join(where)}"
            )
        failed = failed or bool(unknown)
    count = len(catalogue.requirements)
    print(
        f"{'FAIL' if failed else 'OK'}: {count} requirements, {len(issues)} grammar issues"
    )
    return 1 if failed else 0


def _cmd_trace(args: argparse.Namespace, catalogue: Catalogue) -> int:
    results = load_results(args.results)
    report = trace(catalogue.requirements, results, args.gate)
    for gap in report.forward_gaps:
        print(
            f"{gap.requirement.id}: [TRACE-FORWARD] verified at {gap.requirement.level.code}: {gap.reason}"
        )
    for rid, where in report.unknown_ids:
        print(
            f"{rid}: [TRACE-UNKNOWN] cited but not in the catalogue: {', '.join(where)}"
        )
    for rid in report.promotable:
        print(f"{rid}: [TRACE-PROMOTE] planned, but a citing test passes")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(
                render_markdown(catalogue.requirements, results, report) + "\n"
            )
    gated = sum(
        1 for r in catalogue.requirements if r.verified and args.gate.permits(r.level)
    )
    verdict = "OK" if report.ok else "FAIL"
    print(f"{verdict}: gate {args.gate.code}, {gated} verified requirements checked")
    return 0 if report.ok else 1


def _cmd_points(args: argparse.Namespace, catalogue: Catalogue) -> int:
    report = trace(catalogue.requirements, {}, Maturity.MEASURED)
    covered = VISION_POINTS - len(report.uncovered_points)
    print(point_grid(report.uncovered_points))
    print(f"{covered}/{VISION_POINTS} vision points cite a requirement")
    if report.uncovered_points:
        print("uncovered: " + ", ".join(str(p) for p in report.uncovered_points))
    return 0


def _cmd_report(args: argparse.Namespace, catalogue: Catalogue) -> int:
    results: Results = load_results(args.results) if args.results else {}
    report = trace(catalogue.requirements, results, args.gate)
    text = render_markdown(
        catalogue.requirements, results, report, with_results=args.results is not None
    )
    if args.out is None:
        print(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ter-req",
        description="Lint, trace and report on the TER EARS requirement catalogue.",
    )
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    sub = parser.add_subparsers(dest="command", required=True)

    lint = sub.add_parser("lint", help="check EARS grammar and catalogue invariants")
    lint.add_argument(
        "--tests", type=Path, default=None, help="also scan this tree for req markers"
    )
    lint.set_defaults(handler=_cmd_lint)

    gate = sub.add_parser(
        "trace", help="forward/backward trace gate over pytest results"
    )
    gate.add_argument("--results", type=Path, required=True)
    gate.add_argument("--gate", type=Maturity.parse, default=Maturity.MEASURED)
    gate.add_argument(
        "--summary", type=Path, default=None, help="append a Markdown report here"
    )
    gate.set_defaults(handler=_cmd_trace)

    points = sub.add_parser("points", help="list vision points with no requirement")
    points.set_defaults(handler=_cmd_points)

    report = sub.add_parser(
        "report", help="Markdown coverage report per maturity level"
    )
    report.add_argument("--results", type=Path, default=None)
    report.add_argument("--gate", type=Maturity.parse, default=Maturity.MEASURED)
    report.add_argument("--out", type=Path, default=None)
    report.set_defaults(handler=_cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        catalogue = load_catalogue(args.catalogue)
        code: int = args.handler(args, catalogue)
    except (CatalogueError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
