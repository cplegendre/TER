"""Render the requirement trace as Markdown with visual coverage bars.

The output is GitHub-flavoured Markdown: unicode bars that read well in a
terminal, a pull request comment or a job summary, plus Mermaid charts that
GitHub draws natively.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ter.domain.maturity import Maturity
from ter.domain.points import PointKind, PointStatus, VisionPoint, summarise
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


# --------------------------------------------------------------------------
# Vision points index (docs/ter4/points.md)
# --------------------------------------------------------------------------

STATUS_MARK = {
    PointStatus.DONE: "●",
    PointStatus.PARTIAL: "◐",
    PointStatus.NOT_STARTED: "○",
}
STATUS_LABEL = {
    PointStatus.DONE: "done",
    PointStatus.PARTIAL: "partial",
    PointStatus.NOT_STARTED: "not started",
}
SHORT_TEXT = 90
ISSUE_URL = "https://github.com/lgriffin/TER/issues/{number}"


def status_bar(counts: Mapping[PointStatus, int], width: int = BAR_WIDTH) -> str:
    """A bar of ``width`` cells: done █, partial ▓, not started ░."""
    total = sum(counts.values())
    if not total:
        return EMPTY * width
    done = round(width * counts[PointStatus.DONE] / total)
    started = counts[PointStatus.DONE] + counts[PointStatus.PARTIAL]
    partial = round(width * started / total) - done
    return FULL * done + PARTIAL * partial + EMPTY * (width - done - partial)


def status_grid(points: Sequence[VisionPoint], per_row: int = 20) -> str:
    """Every point as ● done, ◐ partial or ○ not started, ``per_row`` per line."""
    ordered = sorted(points, key=lambda p: p.number)
    lines = []
    for start in range(0, len(ordered), per_row):
        row = ordered[start : start + per_row]
        cells = " ".join(STATUS_MARK[p.status] for p in row)
        lines.append(f"P{row[0].number:03d} {cells}")
    return "\n".join(lines)


def _short(text: str, limit: int = SHORT_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _issue(point: VisionPoint) -> str:
    if point.issue is None:
        return ""
    link = f"[#{point.issue}]({ISSUE_URL.format(number=point.issue)})"
    return f"{link} ✓" if point.real_data_verified else link


def _real_data_line(points: Sequence[VisionPoint]) -> str:
    real = [p for p in points if p.real_data]
    verified = sum(1 for p in real if p.real_data_verified)
    issues = sorted({p.issue for p in real if p.issue is not None})
    links = ", ".join(f"[#{n}]({ISSUE_URL.format(number=n)})" for n in issues)
    return (
        f"Real session data: **{len(real)} points** need it ({verified} verified) and "
        f"cannot be done on synthetic tests alone. Tracked in {links or 'no issues'}."
    )


def render_points_index(
    points: Sequence[VisionPoint],
    requirements: Sequence[Requirement],
) -> str:
    """Render docs/ter4/points.md, the living index of the 200 points."""
    by_req = {r.id: r for r in requirements}
    summary = summarise(points)
    counts = " · ".join(
        f"{STATUS_MARK[s]} {STATUS_LABEL[s]} **{summary.by_status[s]}**"
        for s in PointStatus
    )
    out = [
        "# TER 4 vision points",
        "",
        "<!-- Generated by `ter-req points` from requirements/points.yaml. Do not edit;",
        "     CI fails when this file is stale. -->",
        "",
        "Leigh's 200-point vision for TER 4, each with its definition of done, the EARS",
        "rules that enforce it and how it is verified. Rules live in",
        "`requirements/*.yaml`; `✓` marks a verified rule and `·` a planned one. See",
        "[requirements.md](requirements.md) for the controls.",
        "",
        f"**{len(points)} points** · {counts}",
        "",
        f"`{status_bar(summary.by_status, 40)}`",
        "",
        f"Legend: `{FULL}` done · `{PARTIAL}` partial · `{EMPTY}` not started",
        "",
        "## By level",
        "",
        "| Level | Progress | Done | Partial | Not started | Total |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for level, row in summary.by_level.items():
        level_total = sum(row.values())
        if not level_total:
            continue
        out.append(
            f"| {level.code} {level.title} | `{status_bar(row)}` | {row[PointStatus.DONE]} | "
            f"{row[PointStatus.PARTIAL]} | {row[PointStatus.NOT_STARTED]} | {level_total} |"
        )
    kinds = {kind: sum(1 for p in points if p.kind is kind) for kind in PointKind}
    out += [
        "",
        "Kinds: " + " · ".join(f"{k.value} {n}" for k, n in kinds.items()),
        "",
        _real_data_line(points),
        "",
        "## Map",
        "",
        "`●` done · `◐` partial · `○` not started",
        "",
        "```text",
        status_grid(points),
        "```",
        "",
        "## Points",
        "",
        "| Id | Point | Level | Status | Issue | Definition of done | Rules | Verification |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for point in sorted(points, key=lambda p: p.number):
        rules = "<br>".join(
            f"`{rid}` {'✓' if rid in by_req and by_req[rid].verified else '·'}"
            for rid in point.rules
        )
        done = "<br>".join(f"• {_escape(d)}" for d in point.definition_of_done)
        verification = "<br>".join(_escape(str(v)) for v in point.verification)
        out.append(
            f"| {point.id} | {_escape(_short(point.text))} | {point.level.code} | "
            f"{STATUS_MARK[point.status]} {STATUS_LABEL[point.status]} | {_issue(point)} | "
            f"{done} | {rules} | {verification} |"
        )
    return "\n".join(out) + "\n"
