"""Inline SVG chart generation for TER analysis results.

Produces standalone SVG strings with no external dependencies. The drawing
primitives (stat tiles, stacked bar, horizontal bar) and the colour palette
now live in TER 4's reports adapter,
:mod:`ter.adapters.driving.reports`; this module keeps its public API and
maps a ``TERResult`` onto them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from ter.adapters.driving.reports import palette as _palette
from ter.adapters.driving.reports import svg as _svg

from .models import TERResult

# Validated categorical palette (light mode): fixed order, CVD-safe adjacent
# pairs. Defined once in ``ter.adapters.driving.reports.palette``.
PALETTE = list(_palette.PALETTE)


def _esc(text: str) -> str:
    return _svg.esc(text)


def _fmt_tokens(n: int) -> str:
    return _svg.fmt_tokens(n)


def _fmt_pct(val: float) -> str:
    return _svg.fmt_pct(val)


def _fmt_cost(val: float) -> str:
    return f"${val:.4f}"


# ---------------------------------------------------------------------------
# Primitives (delegated to the TER 4 reports adapter)
# ---------------------------------------------------------------------------


def _stacked_bar_svg(
    title: str,
    segments: list[tuple[str, int, str]],
    width: int = 640,
    bar_height: int = 42,
) -> str:
    """Horizontal stacked bar chart. segments: [(label, value, color), ...]."""
    return _svg.stacked_bar(
        title,
        [(label, float(value), color) for label, value, color in segments],
        width=width,
        bar_height=bar_height,
    )


def _horizontal_bar_svg(
    title: str,
    items: list[tuple[str, float, str]],
    width: int = 640,
    bar_height: int = 28,
    format_value: Callable[[float], str] | None = None,
) -> str:
    """Horizontal bar chart. items: [(label, value, color), ...]."""
    return _svg.horizontal_bar(
        title, items, width=width, bar_height=bar_height, format_value=format_value
    )


def _stat_tile_svg(
    metrics: list[tuple[str, str]],
    width: int = 640,
) -> str:
    """Row of stat tiles. metrics: [(label, value_str), ...]."""
    return _svg.stat_tiles(metrics, width=width)


# ---------------------------------------------------------------------------
# Public chart generators from TERResult
# ---------------------------------------------------------------------------


def chart_key_metrics(result: TERResult) -> str:
    """Stat tile row: TER, total tokens, waste %, cost."""
    waste_pct = (
        f"{result.waste_tokens / result.total_tokens * 100:.1f}%"
        if result.total_tokens
        else "0%"
    )
    metrics = [
        ("TER Score", f"{result.aggregate_ter:.2f}"),
        ("Total Tokens", _fmt_tokens(result.total_tokens)),
        ("Waste", waste_pct),
    ]
    if result.economics:
        metrics.append(("Est. Cost", _fmt_cost(result.economics.estimated_cost_usd)))
    if result.uncertainty:
        metrics.append(("Reliability", result.uncertainty.reliability))
    return _stat_tile_svg(metrics)


def chart_composition(result: TERResult) -> str:
    """Stacked bar: token composition by classification label."""
    label_tokens: Counter[str] = Counter()
    for item in result.classified_spans:
        label_tokens[item.label.value] += item.span.token_count

    label_colors = {
        "aligned_reasoning": PALETTE[0],
        "aligned_tool_call": PALETTE[1],
        "aligned_response": PALETTE[2],
        "redundant_reasoning": PALETTE[3],
        "unnecessary_tool_call": PALETTE[4],
        "over_explanation": PALETTE[5],
    }

    segments = []
    for label_val, tokens in label_tokens.most_common():
        color = label_colors.get(label_val, PALETTE[6])
        name = label_val.replace("_", " ").title()
        segments.append((name, tokens, color))

    return _stacked_bar_svg("Token Composition", segments)


def chart_phase_scores(result: TERResult) -> str:
    """Horizontal bar: per-phase TER scores."""
    items = []
    phase_colors = {
        "reasoning": PALETTE[0],
        "tool_use": PALETTE[1],
        "generation": PALETTE[2],
    }
    for phase, score in result.phase_scores.items():
        color = phase_colors.get(phase, PALETTE[3])
        items.append((phase.replace("_", " ").title(), score, color))

    return _horizontal_bar_svg(
        "Phase Scores",
        items,
        format_value=lambda v: f"{v:.3f}",
    )


def chart_waste_patterns(result: TERResult) -> str:
    """Horizontal bar: waste patterns ranked by tokens wasted."""
    if not result.waste_patterns:
        return ""

    by_type: dict[str, int] = {}
    for wp in result.waste_patterns:
        label = wp.pattern_type.replace("_", " ").title()
        by_type[label] = by_type.get(label, 0) + wp.tokens_wasted

    sorted_items = sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:6]
    items = [
        (label, float(tokens), PALETTE[i % len(PALETTE)])
        for i, (label, tokens) in enumerate(sorted_items)
    ]

    return _horizontal_bar_svg(
        "Waste Patterns",
        items,
        format_value=lambda v: _fmt_tokens(int(v)),
    )


def chart_positional_ter(result: TERResult) -> str:
    """Horizontal bar: early/mid/late TER from economics positional breakdown."""
    if not result.economics:
        return ""

    pos = result.economics.positional
    items = [
        ("Early", pos.early_ter, PALETTE[0]),
        ("Mid", pos.mid_ter, PALETTE[1]),
        ("Late", pos.late_ter, PALETTE[2]),
    ]
    return _horizontal_bar_svg(
        "Positional TER (Session Thirds)",
        items,
        format_value=lambda v: f"{v:.3f}",
    )


def chart_economics(result: TERResult) -> str:
    """Stacked bar: token economics (input/output/cache)."""
    if not result.economics:
        return ""

    e = result.economics
    segments = [
        ("Output Tokens", e.total_output_tokens, PALETTE[0]),
        ("Input Tokens", e.total_input_tokens, PALETTE[1]),
        ("Cache Read", e.total_cache_read_tokens, PALETTE[2]),
        ("Cache Write", e.total_cache_creation_tokens, PALETTE[3]),
    ]
    segments = [(label, v, c) for label, v, c in segments if v > 0]
    return _stacked_bar_svg("Token Economics", segments)


def chart_waste_breakdown(result: TERResult) -> str:
    """Stacked bar: aligned vs waste tokens."""
    segments = [
        ("Aligned", result.aligned_tokens, PALETTE[0]),
        ("Waste", result.waste_tokens, PALETTE[1]),
    ]
    return _stacked_bar_svg("Aligned vs Waste Tokens", segments)


def generate_all_charts(result: TERResult) -> dict[str, str]:
    """Generate all available charts, returning {name: svg_string}."""
    charts = {}
    charts["key_metrics"] = chart_key_metrics(result)
    charts["waste_breakdown"] = chart_waste_breakdown(result)

    if result.classified_spans:
        charts["composition"] = chart_composition(result)

    charts["phase_scores"] = chart_phase_scores(result)

    if result.waste_patterns:
        charts["waste_patterns"] = chart_waste_patterns(result)

    if result.economics:
        charts["positional_ter"] = chart_positional_ter(result)
        charts["economics"] = chart_economics(result)

    return {k: v for k, v in charts.items() if v}
