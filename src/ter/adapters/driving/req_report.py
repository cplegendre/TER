"""Render the requirement trace as Markdown with visual coverage bars.

The output is GitHub-flavoured Markdown: unicode bars that read well in a
terminal, a pull request comment or a job summary, plus Mermaid charts that
GitHub draws natively.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ter.domain.maturity import Maturity
from ter.domain.requirements import (
    VISION_POINTS,
    LevelCoverage,
    Requirement,
    TestOutcome,
    TraceReport,
)

BAR_WIDTH = 20
FULL, PARTIAL, EMPTY = "█", "▓", "░"
POINT_ON, POINT_OFF = "■", "·"


def bar(level: LevelCoverage, width: int = BAR_WIDTH) -> str:
    """A bar of ``width`` cells: traced █, verified but untraced ▓, planned ░."""
    if not level.total:
        return EMPTY * width
    traced = round(width * level.traced / level.total)
    verified = round(width * level.verified / level.total) - traced
    return (
        FULL * traced
        + PARTIAL * max(verified, 0)
        + EMPTY * (width - traced - max(verified, 0))
    )


def point_grid(
    uncovered: Sequence[int], total: int = VISION_POINTS, per_row: int = 20
) -> str:
    """A grid of vision points: ■ has a requirement, · has none yet."""
    missing = set(uncovered)
    lines = []
    for start in range(1, total + 1, per_row):
        cells = " ".join(
            POINT_OFF if p in missing else POINT_ON
            for p in range(start, min(start + per_row, total + 1))
        )
        lines.append(f"{start:>3} {cells}")
    return "\n".join(lines)


def _passing(results: Mapping[str, Sequence[TestOutcome]], rid: str) -> tuple[int, int]:
    outcomes = results.get(rid, ())
    return sum(1 for o in outcomes if o.passed), len(outcomes)


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(
    requirements: Sequence[Requirement],
    results: Mapping[str, Sequence[TestOutcome]],
    report: TraceReport,
    *,
    with_results: bool = True,
) -> str:
    """Render the full coverage report."""
    total = len(requirements)
    verified = sum(1 for r in requirements if r.verified)
    traced = sum(level.traced for level in report.levels)
    verdict = "PASS" if report.ok else "FAIL"
    out: list[str] = ["# TER requirements coverage", ""]
    gate = report.gate
    if with_results:
        out.append(
            f"**Gate {gate.code} {gate.title}: {verdict}** · {total} requirements · "
            f"{verified} verified · {traced} traced to passing tests"
        )
    else:
        out.append(
            f"{total} requirements · {verified} verified · no test results supplied"
        )
    out += [
        "",
        f"Legend: `{FULL}` verified and traced · `{PARTIAL}` verified, no passing test · "
        f"`{EMPTY}` planned",
        "",
        "| Level | Coverage | Traced | Verified | Planned |",
        "|---|---|---:|---:|---:|",
    ]
    for level in report.levels:
        marker = " (gate)" if level.level is gate else ""
        out.append(
            f"| {level.level.code} {level.level.title}{marker} | `{bar(level)}` "
            f"{level.ratio:.0%} | {level.traced}/{level.total} | {level.verified} | {level.planned} |"
        )
    out += [
        "",
        "```mermaid",
        "pie showData title Requirement status",
        f'    "Verified and traced" : {traced}',
        f'    "Verified, no passing test" : {verified - traced}',
        f'    "Planned" : {total - verified}',
        "```",
        "",
    ]
    if report.forward_gaps or report.unknown_ids:
        out += ["## Gate failures", ""]
        for gap in report.forward_gaps:
            out.append(
                f"- **{gap.requirement.id}** ({gap.requirement.level.code}): {gap.reason}"
            )
        for rid, where in report.unknown_ids:
            out.append(
                f"- **{rid}** is cited but not in the catalogue: {', '.join(where)}"
            )
        out.append("")
    if report.promotable:
        out += [
            "## Ready to promote",
            "",
            "Planned requirements that already have a passing test: "
            + ", ".join(f"`{rid}`" for rid in report.promotable),
            "",
        ]
    covered = VISION_POINTS - len(report.uncovered_points)
    out += [
        "## Vision point trace",
        "",
        f"{covered} of {VISION_POINTS} vision points cite at least one requirement "
        f"(`{POINT_ON}` covered, `{POINT_OFF}` not yet). Informational only.",
        "",
        "```text",
        point_grid(report.uncovered_points),
        "```",
        "",
        "## Requirements by level",
        "",
    ]
    for maturity in Maturity:
        members = [r for r in requirements if r.level is maturity]
        if not members:
            continue
        out += [
            f"### {maturity.code} {maturity.title}",
            "",
            "| Id | Pattern | Status | Tests | Requirement |",
            "|---|---|---|---:|---|",
        ]
        for r in members:
            passed, cited = _passing(results, r.id)
            tests = f"{passed}/{cited}" if with_results else "–"
            out.append(
                f"| `{r.id}` | {r.pattern.value} | {r.status.value} | {tests} | {_escape(r.text)} |"
            )
        out.append("")
    return "\n".join(out)
