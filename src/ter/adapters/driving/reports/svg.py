"""SVG chart primitives for TER reports.

Every function returns a standalone ``<svg>`` string with no external
references: ``role="img"``, a ``<title>`` and a ``<desc>`` (linked with
``aria-labelledby``), and every session-derived string escaped. Colours come
from :mod:`.palette`; each mark carries both a hex fill and a role class, so
the same markup is correct as a standalone file and theme-aware inside the
HTML report.

Two layers:

* primitives on plain data (stat tiles, stacked bar, horizontal bar), which
  ``ter_calculator.charts`` delegates to;
* report charts on a :class:`~ter.domain.report.SessionReport` (span
  timeline, waste Pareto, positional sparkline, composition, …).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence

from ter.domain.report import (
    PHASE_ORDER,
    SPAN_LABEL_ORDER,
    PositionalTer,
    SessionReport,
    SpanCell,
    WasteByType,
)

from .palette import (
    LABEL_ROLES,
    OTHER_LABEL_ROLE,
    OTHER_PHASE_ROLE,
    PHASE_ROLES,
    on_colour,
    resolve,
)

FONT = "system-ui,-apple-system,Segoe UI,sans-serif"

#: ``(label, value, colour)`` where colour is a palette role or a hex value.
Segment = tuple[str, float, str]

_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_SLUG = re.compile(r"[^a-z0-9]+")
_CHAR_W = 6.2  # average glyph advance at 11px, for layout estimates


# ---------------------------------------------------------------------------
# Formatting and escaping
# ---------------------------------------------------------------------------


def esc(text: object) -> str:
    """Escape text for XML/HTML content or a quoted attribute.

    Drops code points XML 1.0 forbids so a hostile transcript cannot make an
    SVG ill-formed.
    """
    return html.escape(_XML_ILLEGAL.sub("", str(text)), quote=True)


def fmt_tokens(n: float) -> str:
    """Compact token count: 950, 1.2k, 3.4M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def humanise(identifier: str) -> str:
    """``unnecessary_tool_call`` → ``Unnecessary tool call``."""
    words = identifier.replace("_", " ").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else identifier


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "chart"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"


def _paint(colour: str, kind: str = "f") -> str:
    """``fill="#hex" class="ter-f-role"`` attributes for a colour."""
    hex_value, role = resolve(colour)
    attr = "fill" if kind == "f" else "stroke"
    cls = f' class="ter-{kind}-{role}"' if role else ""
    return f'{attr}="{hex_value}"{cls}'


def _fill_stroke(fill: str, stroke: str) -> str:
    """Fill and stroke attributes plus both role classes."""
    fill_hex, fill_role = resolve(fill)
    stroke_hex, stroke_role = resolve(stroke)
    classes = [f"ter-f-{fill_role}" if fill_role else "", ""]
    classes[1] = f"ter-s-{stroke_role}" if stroke_role else ""
    joined = " ".join(c for c in classes if c)
    cls = f' class="{joined}"' if joined else ""
    return f'fill="{fill_hex}" stroke="{stroke_hex}"{cls}'


def _on_paint(colour: str) -> str:
    hex_value, role = on_colour(colour)
    cls = f' class="ter-f-{role}"' if role else ""
    return f'fill="{hex_value}"{cls}'


def _text(
    x: float,
    y: float,
    content: str,
    *,
    role: str = "ink-2",
    size: int = 12,
    weight: int | None = None,
    anchor: str | None = None,
) -> str:
    """A text element whose ``content`` is already escaped."""
    attrs = f'x="{x:.1f}" y="{y:.1f}" {_paint(role)} font-size="{size}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    if anchor:
        attrs += f' text-anchor="{anchor}"'
    return f"<text {attrs}>{content}</text>"


def _open(
    chart_id: str, title: str, desc: str, width: int, height: int
) -> list[str]:
    cid = esc(chart_id)
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" role="img" class="ter-chart-svg"'
        f' aria-labelledby="{cid}-title {cid}-desc" font-family="{FONT}">',
        f'<title id="{cid}-title">{esc(title)}</title>',
        f'<desc id="{cid}-desc">{esc(desc)}</desc>',
        f'<rect width="{width}" height="{height}" rx="8" {_paint("surface")}/>',
    ]


def _heading(title: str, x: float = 16) -> str:
    return _text(x, 24, esc(title), role="ink", size=15, weight=600)


def _legend(
    entries: Sequence[tuple[str, str]],
    *,
    x0: float,
    y0: float,
    max_x: float,
    swatch: str = "rect",
) -> tuple[list[str], float]:
    """Legend rows that wrap at ``max_x``. Returns parts and the bottom y."""
    parts: list[str] = []
    x, y = x0, y0
    for text, colour in entries:
        width = 14 + len(text) * _CHAR_W
        if x > x0 and x + width > max_x:
            x, y = x0, y + 18
        if swatch == "line":
            parts.append(
                f'<line x1="{x:.1f}" y1="{y + 5:.1f}" x2="{x + 10:.1f}"'
                f' y2="{y + 5:.1f}" stroke-width="2" {_paint(colour, "s")}/>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="10" height="10" rx="2"'
                f" {_paint(colour)}/>"
            )
        parts.append(_text(x + 14, y + 9, esc(text), size=11))
        x += width + 16
    return parts, y + 10


# ---------------------------------------------------------------------------
# Primitives on plain data
# ---------------------------------------------------------------------------


def stat_tiles(
    metrics: Sequence[tuple[str, str]],
    *,
    title: str = "Key metrics",
    width: int = 640,
    chart_id: str | None = None,
) -> str:
    """A row of stat tiles: ``[(label, value), ...]``."""
    if not metrics:
        return ""
    height = 80
    tile_w = width / len(metrics)
    desc = "; ".join(f"{label}: {value}" for label, value in metrics)
    parts = _open(chart_id or slug(title), title, desc, width, height)
    for i, (label, value) in enumerate(metrics):
        x = i * tile_w + tile_w / 2
        parts.append(_text(x, 28, esc(label), role="muted", anchor="middle"))
        parts.append(
            _text(x, 58, esc(value), role="ink", size=25, weight=700, anchor="middle")
        )
    parts.append("</svg>")
    return "\n".join(parts)


def stacked_bar(
    title: str,
    segments: Sequence[Segment],
    *,
    width: int = 640,
    bar_height: int = 42,
    desc: str | None = None,
    chart_id: str | None = None,
) -> str:
    """A 100% horizontal bar of ``segments`` with a wrapping legend."""
    total = sum(v for _, v, _ in segments)
    if total <= 0:
        return ""
    top, side, gap = 36, 16, 2
    bar_w = width - 2 * side
    legend_entries = [
        (f"{label} ({fmt_tokens(value)}, {fmt_pct(value / total)})", colour)
        for label, value, colour in segments
    ]
    legend, bottom = _legend(
        legend_entries, x0=side, y0=top + bar_height + 16, max_x=width - side
    )
    height = int(bottom + 16)
    if desc is None:
        desc = f"{title}. " + "; ".join(text for text, _ in legend_entries)
    parts = _open(chart_id or slug(title), title, desc, width, height)
    parts.append(_heading(title, side))
    x = float(side)
    for i, (label, value, colour) in enumerate(segments):
        w = (value / total) * bar_w - (gap if i < len(segments) - 1 else 0)
        if w < 1:
            x += max(w, 0) + gap
            continue
        parts.append(
            f'<rect x="{x:.1f}" y="{top}" width="{w:.1f}" height="{bar_height}"'
            f' rx="4" {_paint(colour)}>'
            f"<title>{esc(label)}: {fmt_tokens(value)} tokens,"
            f" {fmt_pct(value / total)}</title></rect>"
        )
        if w > 50:
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{top + bar_height / 2 + 4:.1f}"'
                f' text-anchor="middle" font-size="12" font-weight="600"'
                f" {_on_paint(colour)}>{fmt_pct(value / total)}</text>"
            )
        x += w + gap
    parts.extend(legend)
    parts.append("</svg>")
    return "\n".join(parts)


def horizontal_bar(
    title: str,
    items: Sequence[Segment],
    *,
    width: int = 640,
    bar_height: int = 28,
    format_value: Callable[[float], str] | None = None,
    domain_max: float | None = None,
    desc: str | None = None,
    chart_id: str | None = None,
) -> str:
    """Horizontal bars, one per ``(label, value, colour)``, value-labelled."""
    if not items:
        return ""
    fmt = format_value or (lambda v: f"{v:.2f}")
    max_val = domain_max or max(v for _, v, _ in items) or 1.0
    top, label_w, right, row_gap = 36, 140, 70, 8
    chart_w = width - label_w - right
    height = top + len(items) * (bar_height + row_gap) + 8
    if desc is None:
        desc = f"{title}. " + "; ".join(f"{label}: {fmt(v)}" for label, v, _ in items)
    parts = _open(chart_id or slug(title), title, desc, width, height)
    parts.append(_heading(title))
    for i, (label, value, colour) in enumerate(items):
        y = top + i * (bar_height + row_gap)
        bar_w = max(value, 0) / max_val * chart_w if max_val > 0 else 0
        parts.append(
            _text(
                label_w - 8,
                y + bar_height / 2 + 4,
                esc(_truncate(label, 21)),
                anchor="end",
            )
        )
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{max(bar_w, 2):.1f}"'
            f' height="{bar_height}" rx="4" {_paint(colour)}>'
            f"<title>{esc(label)}: {esc(fmt(value))}</title></rect>"
        )
        parts.append(
            _text(
                label_w + bar_w + 6,
                y + bar_height / 2 + 4,
                esc(fmt(value)),
                role="ink",
                weight=500,
            )
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Report charts
# ---------------------------------------------------------------------------


def span_timeline(
    spans: Sequence[SpanCell],
    *,
    title: str = "Span timeline",
    width: int = 720,
    chart_id: str = "span-timeline",
) -> str:
    """One cell per classified span, in session order, one lane per phase.

    Aligned cells are blue; waste cells are red with a hatch texture.
    """
    if not spans:
        return ""
    phases = [p for p in PHASE_ORDER] + sorted(
        {s.phase for s in spans} - set(PHASE_ORDER)
    )
    lane_h, lane_gap, top, left, right = 22, 6, 62, 96, 16
    plot_w = width - left - right
    height = top + len(phases) * (lane_h + lane_gap) + 28
    n = len(spans)
    cell_w = plot_w / n
    gap = 1.0 if cell_w >= 4 else 0.0
    waste = [s for s in spans if not s.aligned]
    waste_tokens = sum(s.tokens for s in waste)
    desc = (
        f"{n} classified spans in session order, one lane per phase. "
        f"{n - len(waste)} aligned, {len(waste)} waste "
        f"({fmt_tokens(waste_tokens)} waste tokens)."
    )
    cid = esc(chart_id)
    parts = _open(chart_id, title, desc, width, height)
    parts.append(
        f'<defs><pattern id="{cid}-hatch" width="6" height="6"'
        ' patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#ffffff" stroke-width="2"'
        ' stroke-opacity="0.55"/></pattern></defs>'
    )
    parts.append(_heading(title))
    legend, _ = _legend(
        [("Aligned", "aligned"), ("Waste (hatched)", "waste")],
        x0=16,
        y0=36,
        max_x=width,
    )
    parts.extend(legend)
    # Hatch swatch on the waste legend entry.
    parts.append(
        f'<rect x="{16 + 14 + len("Aligned") * _CHAR_W + 16:.1f}" y="36"'
        f' width="10" height="10" rx="2" fill="url(#{cid}-hatch)"/>'
    )
    lane_y = {p: top + i * (lane_h + lane_gap) for i, p in enumerate(phases)}
    for phase, y in lane_y.items():
        parts.append(_text(left - 8, y + lane_h / 2 + 4, esc(humanise(phase)), anchor="end"))
        parts.append(
            f'<rect x="{left}" y="{y}" width="{plot_w}" height="{lane_h}"'
            f' rx="3" {_paint("grid")} fill-opacity="0.45"/>'
        )
    for i, span in enumerate(spans):
        x = left + i * cell_w
        y = lane_y[span.phase]
        role = "aligned" if span.aligned else "waste"
        w = max(cell_w - gap, 0.6)
        tip = (
            f"Span {span.position}: {humanise(span.label)}, {humanise(span.phase)},"
            f" {span.tokens} tokens, confidence {span.confidence:.2f}"
        )
        parts.append(
            f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{lane_h}"'
            f" {_paint(role)}><title>{esc(tip)}</title></rect>"
        )
        if not span.aligned:
            parts.append(
                f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{lane_h}"'
                f' fill="url(#{cid}-hatch)" pointer-events="none"/>'
            )
    axis_y = top + len(phases) * (lane_h + lane_gap) + 12
    parts.append(_text(left, axis_y, f"span {spans[0].position}", role="muted", size=11))
    parts.append(
        _text(
            width - right,
            axis_y,
            f"span {spans[-1].position}",
            role="muted",
            size=11,
            anchor="end",
        )
    )
    parts.append(
        _text(
            left + plot_w / 2,
            axis_y,
            "session order →",
            role="muted",
            size=11,
            anchor="middle",
        )
    )
    parts.append("</svg>")
    return "\n".join(parts)


def waste_pareto(
    entries: Sequence[WasteByType],
    *,
    title: str = "Waste patterns (Pareto)",
    width: int = 720,
    chart_id: str = "waste-pareto",
) -> str:
    """Waste pattern types as sorted bars, with the cumulative share as a line.

    Bars and line share one axis (share of pattern waste, 0 to 100%), so the
    chart needs no second scale.
    """
    entries = sorted(entries, key=lambda e: (-e.tokens, e.pattern_type))
    total = sum(e.tokens for e in entries)
    if not entries or total <= 0:
        return ""
    top, left, right, plot_h = 58, 48, 16, 130
    plot_w = width - left - right
    height = top + plot_h + 48
    slot = plot_w / len(entries)
    bar_w = min(72.0, slot * 0.62)
    base_y = top + plot_h
    cumulative: list[float] = []
    running = 0
    for e in entries:
        running += e.tokens
        cumulative.append(running / total)
    desc = f"{fmt_tokens(total)} tokens in detected waste patterns. " + "; ".join(
        f"{humanise(e.pattern_type)}: {fmt_tokens(e.tokens)}"
        f" ({fmt_pct(e.tokens / total)}, cumulative {fmt_pct(c)})"
        for e, c in zip(entries, cumulative, strict=True)
    )
    parts = _open(chart_id, title, desc, width, height)
    parts.append(_heading(title))
    legend, _ = _legend([("Waste tokens, share", "waste")], x0=16, y0=34, max_x=width)
    parts.extend(legend)
    line_legend, _ = _legend(
        [("Cumulative share", "ink-2")],
        x0=16 + 14 + len("Waste tokens, share") * _CHAR_W + 16,
        y0=34,
        max_x=width,
        swatch="line",
    )
    parts.extend(line_legend)
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = base_y - tick * plot_h
        role = "baseline" if tick == 0 else "grid"
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"'
            f' stroke-width="1" {_paint(role, "s")}/>'
        )
        parts.append(
            _text(left - 6, y + 4, f"{tick * 100:.0f}%", role="muted", size=11, anchor="end")
        )
    points: list[tuple[float, float]] = []
    max_chars = max(4, int(slot / _CHAR_W))
    for i, (e, cum) in enumerate(zip(entries, cumulative, strict=True)):
        cx = left + slot * (i + 0.5)
        share = e.tokens / total
        h = max(share * plot_h, 2)
        parts.append(
            f'<path d="M{cx - bar_w / 2:.1f},{base_y}'
            f" V{base_y - h + 4:.1f} q0,-4 4,-4 H{cx + bar_w / 2 - 4:.1f}"
            f' q4,0 4,4 V{base_y} Z" {_paint("waste")}>'
            f"<title>{esc(humanise(e.pattern_type))}: {fmt_tokens(e.tokens)} tokens"
            f" in {e.occurrences} occurrence(s), {fmt_pct(share)} of pattern waste,"
            f" cumulative {fmt_pct(cum)}</title></path>"
        )
        parts.append(
            _text(
                cx,
                base_y + 31,
                f"{fmt_tokens(e.tokens)} &#183; {fmt_pct(share, 0)}",
                role="ink",
                size=11,
                weight=600,
                anchor="middle",
            )
        )
        parts.append(
            _text(
                cx,
                base_y + 16,
                esc(_truncate(humanise(e.pattern_type), max_chars)),
                size=11,
                anchor="middle",
            )
        )
        points.append((cx, base_y - cum * plot_h))
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points)
    )
    parts.append(
        f'<path d="{path}" fill="none" stroke-width="2" stroke-linejoin="round"'
        f' {_paint("ink-2", "s")}/>'
    )
    for (x, y), cum in zip(points, cumulative, strict=True):
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" stroke-width="2"'
            f" {_fill_stroke('ink-2', 'surface')}>"
            f"<title>Cumulative {fmt_pct(cum)}</title></circle>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def positional_sparkline(
    positional: PositionalTer,
    *,
    session_ter: float | None = None,
    title: str = "TER across the session",
    width: int = 360,
    chart_id: str = "positional-ter",
) -> str:
    """Early, middle and late TER as a three-point line on a 0 to 1 scale."""
    top, left, right, plot_h = 46, 40, 56, 96
    plot_w = width - left - right
    height = top + plot_h + 34
    base_y = top + plot_h
    names = ("Early", "Mid", "Late")
    counts = (positional.early_spans, positional.mid_spans, positional.late_spans)
    desc = "TER by session third. " + "; ".join(
        f"{n}: {v:.3f} over {c} spans"
        for n, v, c in zip(names, positional.values, counts, strict=True)
    )
    if session_ter is not None:
        desc += f". Session TER {session_ter:.3f}."
    parts = _open(chart_id, title, desc, width, height)
    parts.append(_heading(title))
    for tick in (0.0, 0.5, 1.0):
        y = base_y - tick * plot_h
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"'
            f' stroke-width="1" {_paint("baseline" if tick == 0 else "grid", "s")}/>'
        )
        parts.append(_text(left - 6, y + 4, f"{tick:.1f}", role="muted", size=11, anchor="end"))
    if session_ter is not None:
        y = base_y - max(0.0, min(1.0, session_ter)) * plot_h
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"'
            f' stroke-width="1" stroke-dasharray="4 3" {_paint("muted", "s")}/>'
        )
        parts.append(
            _text(width - right + 6, y - 2, "session", role="muted", size=10)
        )
        parts.append(
            _text(width - right + 6, y + 10, f"{session_ter:.2f}", role="muted", size=10)
        )
    points = [
        (left + plot_w * (i + 0.5) / 3, base_y - max(0.0, min(1.0, v)) * plot_h)
        for i, v in enumerate(positional.values)
    ]
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points)
    )
    parts.append(
        f'<path d="{path}" fill="none" stroke-width="2" stroke-linejoin="round"'
        f' {_paint("series-1", "s")}/>'
    )
    for (x, y), name, value, count in zip(
        points, names, positional.values, counts, strict=True
    ):
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" {_paint("series-1")}>'
            f"<title>{name}: TER {value:.3f} over {count} spans</title></circle>"
        )
        label_y = y - 9 if y > top + 12 else y + 18
        # A surface-coloured halo keeps the value legible over the reference line.
        parts.append(
            f'<text x="{x:.1f}" y="{label_y:.1f}" font-size="11" font-weight="600"'
            f' text-anchor="middle" stroke-width="3" paint-order="stroke"'
            f" {_fill_stroke('ink', 'surface')}>{value:.2f}</text>"
        )
        parts.append(_text(x, base_y + 16, name, size=11, anchor="middle"))
    parts.append("</svg>")
    return "\n".join(parts)


def composition_bar(report: SessionReport, *, width: int = 720) -> str:
    """Scored tokens by classification label, as a 100% bar."""
    order = {label: i for i, label in enumerate(SPAN_LABEL_ORDER)}
    items = sorted(
        (c for c in report.composition if c.tokens > 0),
        key=lambda c: (order.get(c.label, len(order)), c.label),
    )
    segments = [
        (humanise(c.label), float(c.tokens), LABEL_ROLES.get(c.label, OTHER_LABEL_ROLE))
        for c in items
    ]
    return stacked_bar(
        "Token composition by label",
        segments,
        width=width,
        chart_id="token-composition",
    )


def aligned_waste_bar(report: SessionReport, *, width: int = 720) -> str:
    m = report.metrics
    return stacked_bar(
        "Aligned vs waste tokens",
        [
            ("Aligned", float(m.aligned_tokens), "aligned"),
            ("Waste", float(m.waste_tokens), "waste"),
        ],
        width=width,
        chart_id="aligned-waste",
    )


def phase_scores_bar(report: SessionReport, *, width: int = 360) -> str:
    order = {p: i for i, p in enumerate(PHASE_ORDER)}
    phases = sorted(report.phases, key=lambda p: (order.get(p.phase, 9), p.phase))
    items = [
        (humanise(p.phase), p.score, PHASE_ROLES.get(p.phase, OTHER_PHASE_ROLE))
        for p in phases
    ]
    return horizontal_bar(
        "TER by phase",
        items,
        width=width,
        format_value=lambda v: f"{v:.3f}",
        domain_max=1.0,
        chart_id="phase-scores",
    )


def economics_bar(report: SessionReport, *, width: int = 720) -> str:
    e = report.economics
    if e is None:
        return ""
    segments = [
        (label, float(v), colour)
        for label, v, colour in (
            ("Output", e.output_tokens, "series-1"),
            ("Input", e.input_tokens, "series-2"),
            ("Cache read", e.cache_read_tokens, "series-3"),
            ("Cache write", e.cache_write_tokens, "series-4"),
        )
        if v > 0
    ]
    return stacked_bar(
        "Provider token volume", segments, width=width, chart_id="token-economics"
    )


def key_metric_tiles(report: SessionReport, *, width: int = 720) -> str:
    m = report.metrics
    metrics = [
        ("TER", f"{m.ter:.2f}"),
        ("Scored tokens", fmt_tokens(m.total_tokens)),
        ("Waste", fmt_pct(m.waste_share)),
    ]
    if report.economics is not None:
        metrics.append(("Est. cost", f"${report.economics.cost_usd:.4f}"))
    if report.uncertainty is not None:
        metrics.append(("Reliability", report.uncertainty.reliability.value))
    return stat_tiles(metrics, width=width, chart_id="key-metrics")


def report_charts(report: SessionReport) -> dict[str, str]:
    """Every chart the report can draw, by stable name; empty charts omitted."""
    charts = {
        "key_metrics": key_metric_tiles(report),
        "aligned_waste": aligned_waste_bar(report),
        "composition": composition_bar(report),
        "span_timeline": span_timeline(report.spans),
        "phase_scores": phase_scores_bar(report),
        "positional_ter": (
            positional_sparkline(report.positional, session_ter=report.metrics.ter)
            if report.positional is not None
            else ""
        ),
        "waste_pareto": waste_pareto(report.waste_by_type()),
        "economics": economics_bar(report),
    }
    return {name: svg for name, svg in charts.items() if svg}
