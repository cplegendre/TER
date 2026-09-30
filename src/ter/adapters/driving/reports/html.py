"""Self-contained HTML session report.

One file, no scripts and no external requests: inline CSS and inline SVG
charts, a Content-Security-Policy that forbids fetching anything, light and
dark themes through ``prefers-color-scheme``, a single-column layout on
phones, and print styles. Every session-derived string is escaped.
"""

from __future__ import annotations

from ter.domain.report import SessionReport, UncertaintySummary

from .palette import stylesheet
from .svg import (
    composition_bar,
    economics_bar,
    esc,
    fmt_pct,
    fmt_tokens,
    humanise,
    phase_scores_bar,
    positional_sparkline,
    span_timeline,
    waste_pareto,
)

_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

_CSS = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ter-page);color:var(--ter-ink);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:32px 24px 48px}
header{margin-bottom:24px}
.eyebrow{margin:0;color:var(--ter-muted);font-size:13px;letter-spacing:.06em;
text-transform:uppercase;font-weight:600}
h1{margin:4px 0 6px;font-size:30px;line-height:1.2}
h2{margin:0 0 12px;font-size:17px}
.meta{margin:0;color:var(--ter-ink-2)}
code{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
overflow-wrap:anywhere}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
gap:12px;margin-bottom:16px}
.kpi{background:var(--ter-surface);border:1px solid var(--ter-grid);
border-radius:12px;padding:14px 16px}
.kpi span{display:block;color:var(--ter-muted);font-size:13px}
.kpi b{display:block;font-size:28px;line-height:1.2;margin:2px 0;
font-variant-numeric:tabular-nums}
.kpi small{color:var(--ter-ink-2);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card{background:var(--ter-surface);border:1px solid var(--ter-grid);
border-radius:12px;padding:16px;margin:0}
.wide{grid-column:1/-1}
.chart{overflow-x:auto}
.chart svg{display:block;width:100%;height:auto}
.wide .chart svg{min-width:540px}
.half .chart svg{min-width:300px}
figcaption{color:var(--ter-ink-2);font-size:13px;margin-top:8px}
section.card,footer.card{margin-top:16px}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--ter-grid);
vertical-align:top}
th{color:var(--ter-ink-2);font-weight:600;font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.note{border-left:4px solid var(--ter-series-1)}
.note p,.prose p{margin:0 0 8px}
.empty{color:var(--ter-muted);margin:0}
dl{display:grid;grid-template-columns:minmax(120px,max-content) 1fr;gap:6px 18px;
margin:0}
dt{font-weight:600}
dd{margin:0;color:var(--ter-ink-2)}
@media (max-width:760px){
main{padding:20px 16px 32px}
h1{font-size:25px}
.grid{grid-template-columns:minmax(0,1fr)}
.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
.kpi b{font-size:23px}
dl{grid-template-columns:1fr}
dd{margin-bottom:6px}
}
@media print{
body{background:#fff;font-size:12px}
main{max-width:none;padding:0}
.card,.kpi{break-inside:avoid;border-color:#c3c2b7}
.chart{overflow:visible}
.wide .chart svg,.half .chart svg{min-width:0}
.grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
"""

_PAGE_VARS = (
    ":root{--ter-page:#f3f2ee}"
    "@media (prefers-color-scheme: dark){:root:not([data-theme=light])"
    "{--ter-page:#0f0f0e}}"
    ":root[data-theme=dark]{--ter-page:#0f0f0e}"
)

_HOW_TO_READ: tuple[tuple[str, str], ...] = (
    (
        "TER",
        "Token Efficiency Ratio: the share of agent-generated tokens that "
        "served the developer's stated intent, weighted by phase. 1.0 means "
        "every scored token was aligned. User-authored text is never scored.",
    ),
    (
        "Aligned and waste",
        "Every scored span gets one label. Aligned labels are reasoning, tool "
        "calls and responses that serve the intent; waste labels are redundant "
        "reasoning, unnecessary tool calls and over-explanation.",
    ),
    (
        "Span timeline",
        "One cell per classified span, left to right in session order, in the "
        "lane of its phase. Blue cells are aligned; red hatched cells are "
        "waste. Clusters of red show where the session lost its way.",
    ),
    (
        "Waste Pareto",
        "Detected waste patterns grouped by type, largest first. The line is "
        "the running share, so the types left of where it crosses 80% are "
        "the few worth fixing first.",
    ),
    (
        "Session thirds",
        "TER over the early, middle and late third of the spans. A falling "
        "line usually means context drift or rework late in the session.",
    ),
    (
        "Cost",
        "Estimated from provider-reported token usage at the configured rates; "
        "waste cost scales the output cost by the waste share.",
    ),
)


def _kpis(report: SessionReport) -> str:
    m = report.metrics
    u = report.uncertainty
    tiles: list[tuple[str, str, str]] = []
    interval = (
        f"{u.confidence_level:.0%} interval {u.lower:.2f} to {u.upper:.2f}"
        if u is not None
        else f"raw ratio {m.raw_ratio:.2f}"
    )
    tiles.append(("TER", f"{m.ter:.2f}", interval))
    tiles.append(
        (
            "Aligned tokens",
            fmt_tokens(m.aligned_tokens),
            f"of {m.total_tokens:,} scored",
        )
    )
    tiles.append(
        (
            "Waste tokens",
            fmt_tokens(m.waste_tokens),
            f"{fmt_pct(m.waste_share)} of scored tokens",
        )
    )
    if report.economics is not None:
        e = report.economics
        tiles.append(
            ("Est. cost", f"${e.cost_usd:.4f}", f"waste ${e.waste_cost_usd:.4f}")
        )
    patterns = len(report.waste_patterns)
    tiles.append(
        (
            "Waste patterns",
            str(patterns),
            f"{fmt_tokens(report.pattern_waste_tokens)} tokens involved",
        )
    )
    if u is not None:
        tiles.append(
            (
                "Reliability",
                humanise(u.reliability.value),
                f"mean confidence {u.mean_confidence:.2f}",
            )
        )
    cards = "\n".join(
        f'<div class="kpi"><span>{esc(label)}</span><b>{esc(value)}</b>'
        f"<small>{esc(sub)}</small></div>"
        for label, value, sub in tiles
    )
    return f'<section class="kpis" aria-label="Key metrics">\n{cards}\n</section>'


def _figure(svg: str, caption: str, css_class: str) -> str:
    if not svg:
        return ""
    return (
        f'<figure class="card {css_class}"><div class="chart">{svg}</div>'
        f"<figcaption>{esc(caption)}</figcaption></figure>"
    )


def _charts(report: SessionReport) -> str:
    figures = [
        _figure(
            composition_bar(report),
            "Scored tokens by classification label, aligned labels first.",
            "wide",
        ),
        _figure(
            span_timeline(report.spans),
            "Each cell is one span; hover a cell for its label, tokens and confidence.",
            "wide",
        ),
        _figure(
            phase_scores_bar(report),
            "TER within each phase, on a 0 to 1 scale.",
            "half",
        ),
        _figure(
            positional_sparkline(report.positional, session_ter=report.metrics.ter)
            if report.positional is not None
            else "",
            "TER in each third of the session; dashed line is the session TER.",
            "half",
        ),
        _figure(
            waste_pareto(report.waste_by_type()),
            "Waste pattern types by tokens involved, with the cumulative share.",
            "wide",
        ),
        _figure(
            economics_bar(report),
            "Provider-reported tokens for the whole session, including context.",
            "wide",
        ),
    ]
    body = "\n".join(f for f in figures if f)
    return f'<div class="grid">\n{body}\n</div>'


def _waste_table(report: SessionReport) -> str:
    if not report.waste_patterns:
        rows = '<p class="empty">No waste patterns were detected in this session.</p>'
    else:
        ordered = sorted(
            report.waste_patterns,
            key=lambda w: (-w.tokens_wasted, w.start_position, w.pattern_type),
        )
        body = "\n".join(
            "<tr>"
            f"<td>{esc(humanise(w.pattern_type))}</td>"
            f"<td>{esc(w.description)}</td>"
            f'<td class="num">{w.start_position}&#8211;{w.end_position}</td>'
            f'<td class="num">{w.spans_involved}</td>'
            f'<td class="num">{w.tokens_wasted:,}</td>'
            "</tr>"
            for w in ordered
        )
        rows = (
            '<div class="table-wrap"><table><thead><tr><th scope="col">Pattern</th>'
            '<th scope="col">What happened</th><th scope="col" class="num">Span range</th>'
            '<th scope="col" class="num">Spans</th>'
            '<th scope="col" class="num">Tokens</th></tr></thead>'
            f"<tbody>\n{body}\n</tbody></table></div>"
        )
    return f'<section class="card" id="waste-patterns"><h2>Waste patterns</h2>\n{rows}\n</section>'


def uncertainty_note(ter: float, u: UncertaintySummary | None) -> str:
    """Plain-language statement of how far to trust the headline TER."""
    caveat = "TER labels are heuristic estimates, not human-validated ground truth."
    if u is None:
        return (
            f"This analysis carries no uncertainty estimate for the TER of {ter:.2f}, "
            f"so treat small differences between sessions with caution. {caveat}"
        )
    return (
        f"The TER of {ter:.2f} is an estimate. A {u.confidence_level:.0%} "
        f"{humanise(u.method).lower()} interval puts it between {u.lower:.2f} and "
        f"{u.upper:.2f} (width {u.width:.2f}). {u.low_confidence_tokens:,} tokens "
        f"({fmt_pct(u.low_confidence_share)}) were classified with low confidence; "
        f"reliability is {u.reliability.value}. Differences between sessions "
        f"smaller than this interval are noise. {caveat}"
    )


def _sections(report: SessionReport) -> str:
    return "\n".join(
        f'<section class="card prose" id="section-{esc(s.key)}"><h2>{esc(s.title)}</h2>'
        + "".join(f"<p>{esc(p)}</p>" for p in s.paragraphs)
        + "</section>"
        for s in report.sections
    )


def _footer() -> str:
    items = "\n".join(f"<dt>{esc(t)}</dt><dd>{esc(d)}</dd>" for t, d in _HOW_TO_READ)
    return (
        '<footer class="card" id="how-to-read"><h2>How to read this report</h2>'
        f"<dl>\n{items}\n</dl></footer>"
    )


def render_report_html(report: SessionReport) -> str:
    """Render ``report`` as one self-contained HTML document."""
    sid = esc(report.session_id)
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>TER report: {sid}</title>",
        f"<style>\n{stylesheet('.ter-chart')}\n{_PAGE_VARS}\n{_CSS}</style>",
        "</head>",
        '<body><main class="ter-chart">',
        '<header><p class="eyebrow">TER session report</p>'
        "<h1>Token efficiency</h1>"
        f'<p class="meta">Session <code>{sid}</code> &#183; classifier '
        f"{esc(report.classifier_version)} &#183; "
        f"{len(report.spans)} scored spans</p></header>",
        _kpis(report),
        _charts(report),
        _waste_table(report),
        '<section class="card note" id="uncertainty"><h2>Uncertainty</h2>'
        f"<p>{esc(uncertainty_note(report.metrics.ter, report.uncertainty))}</p>"
        "</section>",
        _sections(report),
        _footer(),
        "</main></body>",
        "</html>",
    ]
    return "\n".join(p for p in parts if p) + "\n"
