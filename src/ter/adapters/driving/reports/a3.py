"""The A3 report: one self-contained HTML page in Toyota A3 order.

Reads only the :class:`~ter.domain.lean.a3.A3Report` view-model. Like the
session report, it carries no scripts and makes no requests (a CSP forbids
them), every chart is an accessible SVG (``role="img"``, ``<title>``,
``<desc>``), colours come from :mod:`.palette` and switch with the reader's
light or dark theme, and every session-derived string is escaped. It prints
on one landscape A3 sheet.
"""

from __future__ import annotations

from collections.abc import Sequence

from ter.domain.lean import (
    LEAN_MEASURES,
    SVE_DEFINITION,
    A3Report,
    ActivityClass,
    Countermeasure,
    Finding,
    FlowState,
    Measure,
    StageSummary,
    ValueStatus,
    WipKind,
)
from ter.domain.events import describe_limit
from ter.domain.lean.model import UNCERTAIN_BELOW, Stage
from ter.domain.report import WasteByType
from ter.domain.outcome import CheckResult

from .palette import stylesheet
from .svg import (
    _fill_stroke,
    _heading,
    _legend,
    _open,
    _paint,
    _text,
    esc,
    fmt_pct,
    fmt_tokens,
    stacked_bar,
    waste_pareto,
)

__all__ = [
    "activity_bar",
    "flow_bar",
    "fmt_seconds",
    "render_a3_html",
    "value_stream_map",
    "wip_chart",
]

_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

#: Colour role of each activity class; uncertain is a separate, hatched slot.
ACTIVITY_ROLES: dict[str, str] = {
    ActivityClass.VALUE_ADDING.value: "series-1",
    ActivityClass.NECESSARY_NON_VALUE_ADDING.value: "series-3",
    ActivityClass.AVOIDABLE.value: "waste",
    "uncertain": "series-4",
}

#: Colour role of each kind of work in progress.
WIP_ROLES: dict[WipKind, str] = {
    WipKind.HYPOTHESES: "series-2",
    WipKind.TASKS: "series-7",
    WipKind.EDITS: "series-1",
    WipKind.FAILURES: "waste",
}

FLOW_ROLES: dict[FlowState, str] = {
    FlowState.PROGRESSING: "series-1",
    FlowState.RECOVERING: "series-3",
    FlowState.REPEATING: "series-2",
    FlowState.REWORKING: "waste",
    FlowState.WAITING: "series-7",
    FlowState.INVENTORY: "series-4",
}


def fmt_seconds(seconds: float) -> str:
    """45s, 3m 20s, 1h 05m."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _hatch(cid: str) -> str:
    return (
        f'<defs><pattern id="{cid}-hatch" width="6" height="6"'
        ' patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#ffffff" stroke-width="2"'
        ' stroke-opacity="0.6"/></pattern></defs>'
    )


def value_stream_map(
    stages: Sequence[StageSummary],
    *,
    title: str = "Agentic value stream",
    width: int = 1040,
    chart_id: str = "value-stream",
) -> str:
    """Stages as process boxes, left to right, with tokens, time and waste.

    A red outline and badge mark stages where findings claimed waste; the bar
    in each box shows the stage's avoidable share (red) and uncertain share
    (hatched yellow) of its generated tokens.
    """
    if not stages:
        return ""
    side, gap, top = 16, 26, 58
    box_w = (width - 2 * side - gap * (len(stages) - 1)) / len(stages)
    box_h = 148
    height = top + box_h + 44
    total_tokens = sum(s.tokens for s in stages)
    denominator = total_tokens or 1  # for shares only; the total shown stays real
    desc = "Value stream, in order. " + "; ".join(
        f"{s.stage.label}: {s.steps} steps, {s.tokens} generated tokens, "
        f"{s.context_tokens} context tokens, {fmt_seconds(s.seconds)}, "
        f"{s.avoidable_tokens} avoidable and {s.uncertain_tokens} uncertain tokens, "
        f"{len(s.findings)} findings"
        for s in stages
    )
    cid = esc(chart_id)
    parts = _open(chart_id, title, desc, width, height)
    parts.append(_hatch(cid))
    parts.append(
        '<defs><marker id="' + cid + '-arrow" viewBox="0 0 10 10" refX="9" refY="5"'
        ' markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" {_paint("muted")}/></marker></defs>'
    )
    parts.append(_text(side, 24, esc(title), role="ink", size=15, weight=600))
    legend_y = 36
    parts.append(
        f'<rect x="{side}" y="{legend_y}" width="10" height="10" rx="2" {_paint("waste")}/>'
    )
    parts.append(
        _text(side + 14, legend_y + 9, "Avoidable (confident findings)", size=11)
    )
    ux = side + 14 + 30 * 6.2 + 16
    parts.append(
        f'<rect x="{ux:.1f}" y="{legend_y}" width="10" height="10" rx="2" {_paint("series-4")}/>'
        f'<rect x="{ux:.1f}" y="{legend_y}" width="10" height="10" rx="2" fill="url(#{cid}-hatch)"/>'
    )
    parts.append(_text(ux + 14, legend_y + 9, "Uncertain (verify)", size=11))
    parts.append(
        _text(
            width - side,
            legend_y + 9,
            "developer intent → verified response",
            role="muted",
            size=11,
            anchor="end",
        )
    )
    for i, s in enumerate(stages):
        x = side + i * (box_w + gap)
        flagged = s.avoidable_tokens > 0
        stroke = "waste" if flagged else "baseline"
        stroke_w = 2 if flagged else 1
        tip = (
            f"{s.stage.label}: {s.steps} steps, {s.tokens} generated and "
            f"{s.context_tokens} context tokens, {fmt_seconds(s.seconds)}; "
            f"findings: {', '.join(s.findings) or 'none'}"
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{top}" width="{box_w:.1f}" height="{box_h}" rx="8"'
            f' stroke-width="{stroke_w}" {_fill_stroke("surface", stroke)}>'
            f"<title>{esc(tip)}</title></rect>"
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{top}" width="{box_w:.1f}" height="26" rx="8" {_paint("grid")}/>'
            f'<rect x="{x:.1f}" y="{top + 18}" width="{box_w:.1f}" height="8" {_paint("grid")}/>'
        )
        parts.append(
            _text(x + 10, top + 18, esc(s.stage.label), role="ink", size=13, weight=700)
        )
        if s.findings:
            bx = x + box_w - 16
            role = "waste" if flagged else "muted"
            parts.append(
                f'<circle cx="{bx:.1f}" cy="{top + 13}" r="9" {_paint(role)}>'
                f"<title>{len(s.findings)} finding(s) touch this stage</title></circle>"
            )
            parts.append(
                f'<text x="{bx:.1f}" y="{top + 17}" text-anchor="middle" font-size="11"'
                f' font-weight="700" fill="#ffffff">{len(s.findings)}</text>'
            )
        if s.stage is Stage.INTENT:
            rows = [
                (f"{s.steps} prompt{'s' if s.steps != 1 else ''}", "ink"),
                ("developer input,", "muted"),
                ("not scored", "muted"),
                (f"{fmt_seconds(s.seconds)} developer", "ink-2"),
            ]
        else:
            rows = [
                (f"{s.steps} step{'s' if s.steps != 1 else ''}", "ink"),
                (f"{fmt_tokens(s.tokens)} generated", "ink-2"),
                (f"{fmt_tokens(s.context_tokens)} context", "ink-2"),
                (
                    f"{fmt_seconds(s.seconds)} · {fmt_pct(s.tokens / denominator, 0)} of tokens",
                    "ink-2",
                ),
            ]
        for j, (text, role) in enumerate(rows):
            parts.append(
                _text(
                    x + 10,
                    top + 46 + j * 17,
                    esc(text),
                    role=role,
                    size=12,
                    weight=600 if j == 0 else None,
                )
            )
        # Waste bar.
        by = top + box_h - 20
        bw = box_w - 20
        parts.append(
            f'<rect x="{x + 10:.1f}" y="{by}" width="{bw:.1f}" height="8" rx="4" {_paint("grid")}/>'
        )
        if s.tokens > 0 and s.stage is not Stage.INTENT:
            a = s.avoidable_tokens / s.tokens * bw
            u = s.uncertain_tokens / s.tokens * bw
            if a > 0:
                parts.append(
                    f'<rect x="{x + 10:.1f}" y="{by}" width="{max(a, 3):.1f}" height="8" rx="4" {_paint("waste")}>'
                    f"<title>{s.avoidable_tokens} avoidable tokens</title></rect>"
                )
            if u > 0:
                ux0 = x + 10 + a
                parts.append(
                    f'<rect x="{ux0:.1f}" y="{by}" width="{max(u, 3):.1f}" height="8" rx="4" {_paint("series-4")}>'
                    f"<title>{s.uncertain_tokens} uncertain tokens</title></rect>"
                    f'<rect x="{ux0:.1f}" y="{by}" width="{max(u, 3):.1f}" height="8" rx="4"'
                    f' fill="url(#{cid}-hatch)" pointer-events="none"/>'
                )
            label = (
                f"waste {fmt_pct(s.avoidable_tokens / s.tokens, 0)}"
                if s.avoidable_tokens
                else ("uncertain only" if s.uncertain_tokens else "no waste found")
            )
            parts.append(_text(x + 10, by - 5, esc(label), role="muted", size=10))
        if i < len(stages) - 1:
            ay = top + box_h / 2
            parts.append(
                f'<line x1="{x + box_w + 3:.1f}" y1="{ay}" x2="{x + box_w + gap - 3:.1f}" y2="{ay}"'
                f' stroke-width="2" marker-end="url(#{cid}-arrow)" {_paint("muted", "s")}/>'
            )
    total_s = sum(s.seconds for s in stages if s.stage is not Stage.INTENT)
    parts.append(
        _text(
            side,
            top + box_h + 26,
            esc(
                f"Agent lead time {fmt_seconds(total_s)} · {fmt_tokens(total_tokens)} generated tokens · "
                "time is the wall-clock gap each event closed (tool time counts toward its request)"
            ),
            role="muted",
            size=11,
        )
    )
    parts.append("</svg>")
    return "\n".join(parts)


def activity_bar(report: A3Report, *, width: int = 520) -> str:
    """Generated tokens by Lean activity class, as a 100% bar."""
    sc = report.analysis.scorecard
    labels = {
        ActivityClass.VALUE_ADDING.value: ActivityClass.VALUE_ADDING.label,
        ActivityClass.NECESSARY_NON_VALUE_ADDING.value: "Necessary NVA",
        ActivityClass.AVOIDABLE.value: ActivityClass.AVOIDABLE.label,
        "uncertain": "Uncertain",
    }
    segments = [
        (labels[k], float(n), ACTIVITY_ROLES[k]) for k, n in sc.activity_tokens if n > 0
    ]
    return stacked_bar(
        "Activity classes (generated tokens)",
        segments,
        width=width,
        chart_id="a3-activity",
    )


def flow_bar(report: A3Report, *, time: bool = False, width: int = 520) -> str:
    """Generated tokens (or agent time) by flow state, as a 100% bar."""
    sc = report.analysis.scorecard
    pairs: Sequence[tuple[FlowState, float]] = (
        sc.flow_seconds if time else [(f, float(n)) for f, n in sc.flow_tokens]
    )
    segments = [(f.label, v, FLOW_ROLES[f]) for f, v in pairs if v > 0]
    what = "agent time, seconds" if time else "generated tokens"
    return stacked_bar(
        f"Flow ({what})",
        segments,
        width=width,
        chart_id="a3-flow-time" if time else "a3-flow",
    )


def wip_chart(report: A3Report, *, width: int = 520) -> str:
    """Unresolved work after every event, stacked by kind, with the peak marked."""
    wip = report.analysis.wip
    peak = wip.peak
    if peak is None or peak.total == 0:
        return ""
    side, top, plot_h = 16, 44, 110
    plot_w = width - 2 * side
    n = len(wip.samples)
    step = plot_w / n
    bar = max(step - (1 if step > 3 else 0), 0.5)
    scale = plot_h / peak.total
    base = top + plot_h
    legend, bottom = _legend(
        [(f"{k.label} (peak {wip.peak_of(k)})", WIP_ROLES[k]) for k in WipKind],
        x0=side,
        y0=base + 14,
        max_x=width - side,
    )
    height = int(bottom + 14)
    peaks = ", ".join(f"{k.value} {wip.peak_of(k)}" for k in WipKind)
    desc = (
        f"Work in progress after each of {n} events. Peak {peak.total} open items "
        f"after event {peak.event_id}; peak by kind: {peaks}."
    )
    parts = _open("a3-wip", "Work in progress", desc, width, height)
    parts.append(_heading(f"Work in progress (peak {peak.total})", side))
    for i, sample in enumerate(wip.samples):
        y = float(base)
        for kind in WipKind:
            count = sample.count(kind)
            if not count:
                continue
            h = count * scale
            y -= h
            parts.append(
                f'<rect x="{side + i * step:.1f}" y="{y:.1f}" width="{bar:.1f}"'
                f' height="{h:.1f}" {_paint(WIP_ROLES[kind])}>'
                f"<title>Event {esc(sample.event_id)}: {count} {kind.value}</title></rect>"
            )
    parts.append(
        f'<line x1="{side}" y1="{top}" x2="{width - side}" y2="{top}"'
        f' stroke-dasharray="4 3" {_paint("muted", "s")}/>'
    )
    parts.append(
        _text(width - side, top - 4, f"peak {peak.total}", role="muted", anchor="end")
    )
    parts.append(
        f'<line x1="{side}" y1="{base}" x2="{width - side}" y2="{base}"'
        f" {_paint('baseline', 's')}/>"
    )
    parts.extend(legend)
    parts.append("</svg>")
    return "\n".join(parts)


def pareto(report: A3Report, *, width: int = 520) -> str:
    entries = [WasteByType(p.waste.value, p.tokens, p.findings) for p in report.pareto]
    return waste_pareto(
        entries,
        title="Waste Pareto (generated tokens, confident findings)",
        width=width,
        chart_id="a3-pareto",
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ter-page);color:var(--ter-ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1320px;margin:0 auto;padding:24px 24px 40px}
header.a3{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;
gap:8px 24px;border-bottom:3px solid var(--ter-ink);padding-bottom:10px;margin-bottom:16px}
.eyebrow{margin:0;color:var(--ter-muted);font-size:12px;letter-spacing:.08em;
text-transform:uppercase;font-weight:700}
h1{margin:2px 0 0;font-size:24px;line-height:1.25;max-width:900px}
.meta{margin:0;color:var(--ter-ink-2);font-size:13px;text-align:right}
code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.sheet{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.box{background:var(--ter-surface);border:1px solid var(--ter-grid);border-radius:10px;
padding:14px 16px;min-width:0}
.full{grid-column:1/-1}
h2{margin:0 0 10px;font-size:15px;display:flex;align-items:center;gap:10px}
h2 .n{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;
border-radius:50%;background:var(--ter-ink);color:var(--ter-surface);font-size:13px;flex:none}
h3{margin:14px 0 6px;font-size:13.5px}
p{margin:0 0 8px}
.problem{border-left:4px solid var(--ter-waste);padding:6px 10px;margin:8px 0 0;
background:var(--ter-page);border-radius:0 6px 6px 0}
blockquote{margin:0 0 8px;padding:8px 12px;border-left:3px solid var(--ter-series-1);
background:var(--ter-page);border-radius:0 6px 6px 0;color:var(--ter-ink-2);white-space:pre-wrap;
max-height:9.5em;overflow:auto}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.kpi{border:1px solid var(--ter-grid);border-radius:8px;padding:10px 12px;min-width:0}
.kpi span{display:block;color:var(--ter-muted);font-size:12px}
.kpi b{display:block;font-size:24px;line-height:1.2;margin:2px 0;font-variant-numeric:tabular-nums}
.kpi small{display:block;color:var(--ter-ink-2);font-size:12px;line-height:1.35}
.chart{overflow-x:auto}
.chart svg{display:block;width:100%;height:auto}
.full .chart svg{min-width:760px}
.charts{display:grid;gap:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--ter-grid);vertical-align:top}
th{color:var(--ter-ink-2);font-weight:600;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td small,th small{display:block;color:var(--ter-ink-2);font-weight:400}
.tag{display:inline-block;border-radius:999px;padding:0 8px;font-size:11.5px;font-weight:600;
border:1px solid var(--ter-grid);white-space:nowrap}
.tag.warn{border-color:var(--ter-series-4);background:color-mix(in srgb,var(--ter-series-4) 18%,transparent)}
.tag.risk{border-color:var(--ter-series-7)}
.tag.waste{border-color:var(--ter-waste)}
.ev{margin-top:4px;font-size:11.5px;color:var(--ter-muted)}
.ev code{display:inline-block;margin:0 4px 2px 0;padding:0 4px;border-radius:4px;
background:var(--ter-page);font-size:11.5px}
.rc td.num{white-space:normal;min-width:120px}
.rc .rank{width:28px;color:var(--ter-muted);font-variant-numeric:tabular-nums}
.rc td.num .tag{margin-bottom:2px}
.cms{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.cm{border:1px solid var(--ter-grid);border-radius:8px;padding:10px 12px;min-width:0}
.cm h3{margin:0 0 4px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.cm .why{color:var(--ter-ink-2);font-size:12.5px}
.cm ol{margin:6px 0 0;padding-left:20px}
.cm li{margin:0 0 6px}
.kind{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--ter-muted)}
pre{margin:4px 0 0;padding:8px 10px;background:var(--ter-page);border:1px solid var(--ter-grid);
border-radius:6px;overflow:auto;max-height:16em;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:pre-wrap;overflow-wrap:anywhere}
details{margin-top:4px}
summary{cursor:pointer;color:var(--ter-ink-2);font-size:12.5px}
.empty{color:var(--ter-muted);margin:0}
.fine{color:var(--ter-muted);font-size:12px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0;font-size:12.5px}
dt{font-weight:600}
dd{margin:0;color:var(--ter-ink-2)}
@media (max-width:900px){
main{padding:16px 16px 28px}
.sheet,.cms{grid-template-columns:minmax(0,1fr)}
.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
.meta{text-align:left}
h1{font-size:21px}
}
@page{size:A3 landscape;margin:10mm}
@media print{
body{background:#fff;font-size:11px}
main{max-width:none;padding:0}
.box,.cm{break-inside:avoid}
.chart{overflow:visible}
.full .chart svg{min-width:0}
pre{max-height:none}
details>summary{display:none}
}
"""

_PAGE_VARS = (
    ":root{--ter-page:#f3f2ee}"
    "@media (prefers-color-scheme: dark){:root:not([data-theme=light])"
    "{--ter-page:#0f0f0e}}"
    ":root[data-theme=dark]{--ter-page:#0f0f0e}"
)


def _box(number: int | None, title: str, body: str, css: str = "") -> str:
    badge = f'<span class="n">{number}</span>' if number is not None else ""
    return (
        f'<section class="box {css}" aria-labelledby="s-{number or esc(title.lower())}">'
        f'<h2 id="s-{number or esc(title.lower())}">{badge}{esc(title)}</h2>{body}</section>'
    )


def _figure(svg: str, caption: str) -> str:
    if not svg:
        return ""
    return f'<figure style="margin:0"><div class="chart">{svg}</div><figcaption class="fine">{esc(caption)}</figcaption></figure>'


def _background(report: A3Report) -> str:
    intents = report.intents
    if intents:
        quotes = "".join(
            f"<blockquote>{esc(t.strip())}</blockquote>" for t in intents[:3]
        )
        if len(intents) > 3:
            quotes += f'<p class="fine">… and {len(intents) - 3} more prompt(s).</p>'
        lead = (
            f"<p>The developer asked for the outcome below"
            f"{' and changed it during the session' if len(intents) > 1 else ''}. "
            "Value is judged against it.</p>"
        )
    else:
        quotes = '<p class="empty">No prompt was recorded.</p>'
        lead = ""
    a = report.analysis
    return (
        lead
        + quotes
        + f'<p class="problem"><b>Problem.</b> {esc(report.problem)}</p>'
        + f'<p class="fine">{a.events} events analysed · session <code>{esc(report.session_id or "-")}</code></p>'
        + "".join(
            f'<p class="fine"><b>Limit.</b> {esc(describe_limit(limit))}</p>'
            for limit in report.usage_limits
        )
    )


def _scorecard(report: A3Report) -> str:
    sc = report.analysis.scorecard
    tiles: list[tuple[str, str, str]] = []
    fe = (
        "n/a"
        if sc.flow_efficiency_tokens is None
        else fmt_pct(sc.flow_efficiency_tokens, 0)
    )
    ft = (
        "no timestamps"
        if sc.flow_efficiency_time is None
        else f"{fmt_pct(sc.flow_efficiency_time, 0)} of agent time"
    )
    tiles.append(
        ("Flow efficiency", fe, f"of generated tokens progressing or iterating; {ft}")
    )
    tiles.append(
        (
            "Waste cost",
            fmt_tokens(sc.waste_tokens + sc.waste_context_tokens),
            f"tokens: {sc.waste_tokens:,} generated, {sc.waste_context_tokens:,} context; "
            f"{fmt_seconds(sc.waste_seconds)} of agent time",
        )
    )
    tiles.append(
        (
            "Value-adding",
            fmt_pct(sc.activity_share(ActivityClass.VALUE_ADDING), 0),
            f"necessary NVA {fmt_pct(sc.activity_share(ActivityClass.NECESSARY_NON_VALUE_ADDING), 0)}, "
            f"avoidable {fmt_pct(sc.activity_share(ActivityClass.AVOIDABLE), 0)}, "
            f"uncertain {fmt_pct(sc.activity_share('uncertain'), 0)}",
        )
    )
    if sc.ter is not None:
        tiles.append(("TER", f"{sc.ter.value:.2f}", sc.ter.method))
    else:
        tiles.append(("TER", "not computed", "run with --ter offline or --ter model"))
    sve = report.value_efficiency
    if sve.status is ValueStatus.UNKNOWN or sve.tokens is None:
        tiles.append(("Software Value Efficiency", "unknown", sve.reason))
    else:
        time = "" if sve.time is None else f", {fmt_pct(sve.time, 0)} of agent time"
        verdict = "" if sve.verdict is None else sve.verdict.value
        tiles.append(
            (
                "Software Value Efficiency",
                fmt_pct(sve.tokens, 0),
                f"value-adding work toward the {verdict} outcome, of generated tokens"
                f"{time}",
            )
        )
    wip = report.analysis.wip
    if wip.peak is not None:
        final = wip.final
        tiles.append(
            (
                "Peak WIP",
                str(wip.peak.total),
                ", ".join(f"{wip.peak_of(k)} {k.value}" for k in WipKind)
                + f" at most; {0 if final is None else final.total} open at the end",
            )
        )
    tiles.append(
        (
            "Findings",
            str(sc.findings),
            f"confident waste; {sc.uncertain_findings} uncertain, {sc.risks} risk(s); "
            f"{sc.iterations} iteration / {sc.rework_cycles} rework cycle(s)",
        )
    )
    if sc.composite is not None:
        parts = " + ".join(f"{n} {v:.2f}" for n, v, _ in sc.composite.components)
        tiles.append(
            (
                "Composite",
                f"{sc.composite.value:.2f}",
                f"= mean of {parts}. Read the parts, not the sum.",
            )
        )
    cards = "".join(
        f'<div class="kpi"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(sub)}</small></div>'
        for label, value, sub in tiles
    )
    return (
        f'<div class="kpis" role="list" aria-label="Scorecard">{cards}</div>'
        '<p class="fine" style="margin-top:8px">Each dimension stands alone: no single score '
        f"hides the others. Findings below confidence {UNCERTAIN_BELOW:.2f} are counted as "
        "uncertain, never as waste. Token minimisation is not a goal: efficiency is value "
        f"delivered per unit of resource. {esc(SVE_DEFINITION)}</p>"
        + _dimensions(report)
    )


def _measure_value(m: Measure) -> str:
    v = m.value
    if v is None:
        return "unknown"
    if isinstance(v, str):
        return v
    if m.unit == "ratio":
        return fmt_pct(float(v), 0)
    if m.unit == "tokens":
        return fmt_tokens(v)
    if m.unit == "seconds":
        return fmt_seconds(float(v))
    return str(v)


def _dimensions(report: A3Report) -> str:
    """The six scorecard dimensions, each with its named measures."""
    rows = "".join(
        f'<tr><th scope="row">{esc(d.dimension.label)}'
        f"<small>{esc(d.dimension.question)}</small></th><td>"
        + " · ".join(
            f"{esc(m.label)} <b>{esc(_measure_value(m))}</b>" for m in d.measures
        )
        + "</td></tr>"
        for d in report.dimensions
    )
    return (
        '<h3>Scorecard dimensions</h3><div class="chart"><table>'
        '<thead><tr><th scope="col">Dimension</th><th scope="col">Measures</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _outcome(report: A3Report) -> str:
    """The verdict, judged apart from the scorecard (no measure reads it)."""
    verdict = report.outcome
    if verdict is None:
        return ""
    required = [r for r in verdict.results if r.check.required]
    passed = sum(
        1 for r in required if r.status is not None and r.status.value == "passed"
    )
    per = report.tokens_per_verified_outcome
    tiles = [
        ("Verdict", verdict.verdict.value, "; ".join(verdict.reasons)),
        (
            "Required checks",
            f"{passed} / {len(required)}",
            f"passed, against “{verdict.contract.name}”",
        ),
        (
            "Tokens per verified outcome",
            "n/a" if per is None else fmt_tokens(round(per)),
            "generated tokens / accepted outcomes"
            if per is not None
            else "no accepted outcome to divide by",
        ),
    ]
    cards = "".join(
        f'<div class="kpi"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(sub)}</small></div>'
        for label, value, sub in tiles
    )
    # Every check with its status and evidence source, open checks first, so
    # an accepted verdict shows what it rests on too.
    ordered = sorted(
        verdict.results,
        key=lambda r: r.status is not None and r.status.value == "passed",
    )
    head = (
        '<thead><tr><th scope="col">Status</th><th scope="col">Check</th>'
        '<th scope="col">Evidence</th></tr></thead>'
    )

    def row(r: CheckResult) -> str:
        status = "no evidence" if r.status is None else r.status.value
        optional = "" if r.check.required else " (optional)"
        sources = ", ".join(e.source for e in r.evidence) or "-"
        detail = next((e.detail for e in r.evidence if e.detail), "")
        return (
            f"<tr><td>{esc(status)}</td><td><code>{esc(r.check.id)}</code>{esc(optional)}"
            + (f"<br><small>{esc(detail)}</small>" if detail else "")
            + f"</td><td><code>{esc(sources)}</code></td></tr>"
        )

    shown, rest = ordered[:8], ordered[8:]
    checks = (
        f'<div class="chart"><table>{head}<tbody>{"".join(row(r) for r in shown)}</tbody></table></div>'
        if shown
        else ""
    )
    if rest:
        checks += (
            f"<details><summary>Show the other {len(rest)} checks</summary>"
            f'<div class="chart"><table>{head}<tbody>{"".join(row(r) for r in rest)}</tbody></table></div></details>'
        )
    return (
        f'<div class="kpis" role="list" aria-label="Outcome">{cards}</div>'
        + checks
        + f'<p class="fine" style="margin-top:8px">Judged from <code>{esc(verdict.run_ref)}</code> '
        f"({esc(verdict.source)}), separately from the scorecard: no measure on this page "
        "reads the verdict.</p>"
    )


def _current_state(report: A3Report) -> str:
    return _figure(
        value_stream_map(report.analysis.value_stream),
        "Each box is a stage of the agentic value stream. Hover a box for its findings.",
    )


def _analysis(report: A3Report) -> str:
    charts = [
        _figure(
            pareto(report),
            "Wastes by tokens claimed (generated and context), with the running share.",
        )
        or '<p class="empty">No confident waste was found, so there is no Pareto to draw.</p>',
        _figure(
            activity_bar(report),
            "Value-adding, necessary but non-value-adding, avoidable, uncertain.",
        ),
        _figure(
            flow_bar(report),
            "Progressing versus repeating, reworking, recovering, waiting, inventory.",
        ),
    ]
    charts.append(
        _figure(
            wip_chart(report),
            "Unresolved hypotheses, tasks, edits and failures after every event.",
        )
    )
    if report.analysis.scorecard.agent_seconds > 0:
        charts.append(
            _figure(
                flow_bar(report, time=True), "The same split for wall-clock agent time."
            )
        )
    cycles = report.analysis.cycles
    note = ""
    if cycles:
        rows = "".join(
            f"<li><code>{esc(c.command)}</code>: {esc(c.verdict.value)}, {esc(c.reason)}.</li>"
            for c in cycles
        )
        note = (
            '<h3>Fail → fix → re-run cycles</h3><ul class="fine">' + rows + "</ul>"
            '<p class="fine">Iteration (the next run passed or failed differently) is productive '
            "and counted as recovering; only an unchanged failure is rework.</p>"
        )
    return f'<div class="charts">{"".join(charts)}</div>{note}'


def _evidence(ids: Sequence[str], limit: int = 6) -> str:
    shown = "".join(f"<code>{esc(i)}</code>" for i in ids[:limit])
    more = f" +{len(ids) - limit} more" if len(ids) > limit else ""
    return f'<div class="ev" aria-label="Evidence event ids">{shown}{more}</div>'


def _root_causes(report: A3Report) -> str:
    if not report.root_causes:
        return '<p class="empty">No findings: every step was classified from its stage alone.</p>'
    rows = "".join(_finding_row(i, f) for i, f in enumerate(report.root_causes, 1))
    hidden = len(report.analysis.findings) - len(report.root_causes)
    tail = (
        f'<p class="fine">{hidden} more finding(s) in the JSON output.</p>'
        if hidden > 0
        else ""
    )
    return (
        '<p class="fine">Largest cost first; uncertain findings and risks after. Each cites the '
        "events it rests on.</p>"
        '<table class="rc"><thead><tr><th scope="col">#</th><th scope="col">Finding and evidence</th>'
        '<th scope="col" class="num">Waste · confidence · cost</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>{tail}"
    )


def _finding_row(i: int, f: Finding) -> str:
    risk = f.kind.value == "risk"
    tag_cls = "risk" if risk else "waste"
    kind = "Risk" if risk else f.waste.label
    unsure = '<span class="tag warn">uncertain</span>' if f.uncertain else ""
    cost = "outcome at risk" if risk else f"{f.tokens + f.context_tokens:,} tokens"
    if not risk and f.seconds:
        cost += f", {fmt_seconds(f.seconds)}"
    return (
        f'<tr><td class="rank">{i}</td><td><b>{esc(f.title)}</b><small>{esc(f.explanation)}</small>'
        f"{_evidence(f.evidence)}</td>"
        f'<td class="num"><span class="tag {tag_cls}">{esc(kind)}</span>'
        f"<small>confidence {f.confidence:.2f}</small>{unsure}<small>{cost}</small></td></tr>"
    )


def _countermeasure(c: Countermeasure) -> str:
    items = []
    for a in c.actions:
        snippet = ""
        if a.snippet:
            if a.language == "json+bash":
                config, _, script = a.snippet.partition("\n\n")
                snippet = (
                    f"<pre><code>{esc(config)}</code></pre>"
                    f"<details><summary>Hook script</summary><pre><code>{esc(script)}</code></pre></details>"
                )
            else:
                snippet = f"<pre><code>{esc(a.snippet)}</code></pre>"
        items.append(
            f'<li><span class="kind">{esc(a.kind.label)}</span> {esc(a.text)}{snippet}</li>'
        )
    unsure = ' <span class="tag warn">verify first</span>' if c.uncertain else ""
    return (
        f'<article class="cm"><h3>{esc(c.title)}{unsure}</h3>'
        f'<p class="why">{esc(c.rationale)} Answers {", ".join(f"<code>{esc(a)}</code>" for a in c.addresses[:3])}'
        f"{'…' if len(c.addresses) > 3 else ''}.</p><ol>{''.join(items)}</ol></article>"
    )


def _countermeasures(report: A3Report) -> str:
    if not report.countermeasures:
        return (
            '<p class="empty">Nothing to change: no detector fired on this session.</p>'
        )
    return (
        '<p class="fine">Derived from the findings above, never from token thresholds. Hook '
        "scripts use documented Claude Code behaviour (exit 2 blocks a PreToolUse call, or "
        "returns stderr to the agent after PostToolUse); adapt paths and commands before use.</p>"
        f'<div class="cms">{"".join(_countermeasure(c) for c in report.countermeasures)}</div>'
    )


def _follow_up(report: A3Report) -> str:
    rows = "".join(
        f'<tr><td>{esc(f.metric)}</td><td class="num">{esc(f.current)}</td>'
        f'<td class="num">{esc(f.target)}</td><td><code>{esc(f.how)}</code></td></tr>'
        for f in report.follow_up
    )
    return (
        '<p class="fine">Run <code>ter a3 &lt;next-session.jsonl&gt; --json</code> and read the '
        "field under How. Targets are directions, not token budgets.</p>"
        '<div class="chart"><table><thead><tr><th scope="col">Measure next run</th>'
        '<th scope="col" class="num">Now</th><th scope="col" class="num">Target</th>'
        f'<th scope="col">How</th></tr></thead><tbody>{rows}</tbody></table></div>'
    )


def _method(report: A3Report) -> str:
    rows = "".join(
        f"<dt><code>{esc(i)}</code></dt><dd>{esc(r)}</dd>"
        for i, _, _, r in report.analysis.detectors
    )
    return (
        '<p class="fine">Every classification cites event ids from the <code>ter.event</code> '
        "stream; the JSON output (<code>--json</code>) carries the per-event basis and the "
        "evidence graph (<code>--graph</code>). Detectors and their confidence rules:</p>"
        f"<details><summary>Show the {len(report.analysis.detectors)} detector rules</summary><dl>{rows}</dl></details>"
        + _lean_concepts()
    )


def _lean_concepts() -> str:
    """Each Lean concept and the measures TER computes for it (TER-LEN-006)."""
    rows = "".join(
        f'<tr><th scope="row">{esc(c.label)}</th><td>'
        + "<br>".join(
            f"{esc(m.name)} <code>{esc(m.source)}</code><small>{esc(m.meaning)}</small>"
            for m in measures
        )
        + "</td></tr>"
        for c, measures in LEAN_MEASURES.items()
    )
    return (
        "<details><summary>Show the Lean concepts and their measures</summary>"
        '<div class="chart"><table><thead><tr><th scope="col">Concept</th>'
        f'<th scope="col">Measures</th></tr></thead><tbody>{rows}</tbody></table></div></details>'
    )


def render_a3_html(report: A3Report) -> str:
    """Render ``report`` as one self-contained HTML document."""
    title = esc(report.title)
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>TER A3: {title}</title>",
        f"<style>\n{stylesheet('.ter-chart')}\n{_PAGE_VARS}\n{_CSS}</style>",
        "</head>",
        '<body><main class="ter-chart">',
        '<header class="a3"><div><p class="eyebrow">TER A3 · Lean analysis of an agent session</p>'
        f"<h1>{title}</h1></div>"
        f'<p class="meta">Session <code>{esc(report.session_id or "-")}</code><br>'
        f"{report.analysis.events} events · schema ter.a3/0.1</p></header>",
        '<div class="sheet">',
        _box(1, "Background", _background(report)),
        _box(None, "Scorecard", _scorecard(report)),
        *([_box(None, "Outcome", _outcome(report))] if report.outcome else []),
        _box(2, "Current state", _current_state(report), "full"),
        _box(3, "Analysis", _analysis(report)),
        _box(4, "Root causes", _root_causes(report)),
        _box(5, "Countermeasures", _countermeasures(report), "full"),
        _box(6, "Follow-up", _follow_up(report)),
        _box(None, "Evidence and method", _method(report)),
        "</div>",
        "</main></body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"
