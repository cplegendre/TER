"""Unit and structural tests for the TER 4 report view-model and renderers."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.reports import (
    render_report_html,
    report_charts,
    uncertainty_note,
)
from ter.adapters.driving.reports import palette, svg
from ter.adapters.driving.reports.ter3 import from_ter_result
from ter.domain.report import (
    KeyMetrics,
    LabelTokens,
    PhaseScore,
    PositionalTer,
    Reliability,
    ReportSection,
    SessionReport,
    SpanCell,
    WasteByType,
    WasteEntry,
)
from ter_calculator import embedding_cache
from ter_calculator.cli import main
from ter_calculator.models import (
    ClassifiedSpan,
    CostModel,
    InputGrowth,
    PositionalBreakdown,
    SessionEconomics,
    SpanLabel,
    SpanPhase,
    TERResult,
    TokenSpan,
    UncertaintyReport,
    WastePattern,
)

SVG_NS = "{http://www.w3.org/2000/svg}"
HOSTILE = "</script><script>alert(1)</script>--><!-- \x01"
SAMPLE = (
    Path(__file__).resolve().parents[2] / "sample_sessions" / "example_session.jsonl"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _span(position: int, phase: SpanPhase, label: SpanLabel, tokens: int):
    return ClassifiedSpan(
        span=TokenSpan(
            text="x",
            phase=phase,
            position=position,
            token_count=tokens,
            source_message_uuid="m",
        ),
        label=label,
        confidence=0.8,
        cosine_similarity=0.7,
    )


def _ter_result(**overrides) -> TERResult:
    economics = SessionEconomics(
        total_input_tokens=5000,
        total_output_tokens=2000,
        total_cache_creation_tokens=0,
        total_cache_read_tokens=3000,
        input_output_ratio=2.5,
        cache_hit_rate=0.75,
        estimated_cost_usd=0.05,
        estimated_waste_cost_usd=0.01,
        cost_model=CostModel(),
        positional=PositionalBreakdown(0.9, 0.6, 1.0, 2, 2, 2),
        input_growth=InputGrowth([1, 2], 1.0, False, False),
    )
    values = dict(
        session_id="s-1",
        aggregate_ter=0.7,
        raw_ratio=0.72,
        phase_scores={"reasoning": 0.5, "tool_use": 0.8, "generation": 0.9},
        total_tokens=600,
        aligned_tokens=420,
        waste_tokens=180,
        classified_spans=[
            _span(0, SpanPhase.REASONING, SpanLabel.ALIGNED_REASONING, 100),
            _span(1, SpanPhase.TOOL_USE, SpanLabel.ALIGNED_TOOL_CALL, 200),
            _span(2, SpanPhase.REASONING, SpanLabel.REDUNDANT_REASONING, 80),
            _span(3, SpanPhase.TOOL_USE, SpanLabel.UNNECESSARY_TOOL_CALL, 60),
            _span(4, SpanPhase.GENERATION, SpanLabel.OVER_EXPLANATION, 40),
            _span(5, SpanPhase.GENERATION, SpanLabel.ALIGNED_RESPONSE, 120),
        ],
        waste_patterns=[
            WastePattern("reasoning_loop", "loop A", 2, 3, 2, 80),
            WastePattern("duplicate_tool_call", "dup read", 3, 3, 1, 60),
            WastePattern("reasoning_loop", "loop B", 4, 4, 1, 40),
            WastePattern("context_restatement", "restated", 5, 5, 1, 10),
        ],
        economics=economics,
        uncertainty=UncertaintyReport(
            mean_confidence=0.8,
            token_weighted_confidence=0.8,
            low_confidence_tokens=60,
            low_confidence_share=0.1,
            interval_lower=0.6,
            interval_upper=0.8,
            bootstrap_samples=100,
            span_count=6,
            reliability="moderate",
        ),
    )
    values.update(overrides)
    return TERResult(**values)


def _minimal_report(**overrides) -> SessionReport:
    base = SessionReport(
        session_id="empty",
        classifier_version="v11",
        metrics=KeyMetrics(0.0, 0.0, 0, 0, 0),
    )
    return replace(base, **overrides)


def _hostile_report() -> SessionReport:
    report = from_ter_result(_ter_result(session_id=HOSTILE))
    return replace(
        report,
        waste_patterns=(WasteEntry(HOSTILE, HOSTILE, 1, 2, 2, 50),),
        composition=(*report.composition, LabelTokens(HOSTILE, 5)),
        spans=(*report.spans, SpanCell(9, HOSTILE, HOSTILE, 5, 0.5)),
        phases=(*report.phases, PhaseScore(HOSTILE, 0.5)),
        sections=(ReportSection(HOSTILE, HOSTILE, (HOSTILE,)),),
    )


def _parse_svg(markup: str) -> ET.Element:
    root = ET.fromstring(markup)
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("role") == "img"
    title = root.find(f"{SVG_NS}title")
    desc = root.find(f"{SVG_NS}desc")
    assert title is not None and (title.text or "").strip()
    assert desc is not None and (desc.text or "").strip()
    labelled = (root.get("aria-labelledby") or "").split()
    assert labelled == [title.get("id"), desc.get("id")]
    return root


# ---------------------------------------------------------------------------
# Domain view-model
# ---------------------------------------------------------------------------


class TestViewModel:
    def test_waste_by_type_groups_and_sorts_largest_first(self) -> None:
        report = from_ter_result(_ter_result())
        assert report.waste_by_type() == (
            WasteByType("reasoning_loop", 120, 2),
            WasteByType("duplicate_tool_call", 60, 1),
            WasteByType("context_restatement", 10, 1),
        )
        assert report.pattern_waste_tokens == 190

    def test_waste_share_is_zero_without_tokens(self) -> None:
        assert KeyMetrics(0.0, 0.0, 0, 0, 0).waste_share == 0.0
        assert KeyMetrics(0.5, 0.5, 10, 7, 3).waste_share == pytest.approx(0.3)

    def test_reliability_parses_unknown_values(self) -> None:
        assert Reliability.parse("HIGH") is Reliability.HIGH
        assert Reliability.parse("shaky") is Reliability.UNKNOWN

    def test_span_and_label_alignment(self) -> None:
        assert SpanCell(0, "reasoning", "aligned_reasoning", 1, 1.0).aligned
        assert not LabelTokens("over_explanation", 1).aligned
        assert PositionalTer(0.1, 0.2, 0.3).values == (0.1, 0.2, 0.3)


# ---------------------------------------------------------------------------
# TER 3 mapper
# ---------------------------------------------------------------------------


class TestFromTerResult:
    @pytest.mark.req("TER-RPT-001")
    def test_maps_metrics_composition_phases_and_spans(self) -> None:
        report = from_ter_result(_ter_result())
        assert report.session_id == "s-1"
        assert report.metrics == KeyMetrics(0.7, 0.72, 600, 420, 180)
        assert dict((c.label, c.tokens) for c in report.composition) == {
            "aligned_reasoning": 100,
            "aligned_tool_call": 200,
            "aligned_response": 120,
            "redundant_reasoning": 80,
            "unnecessary_tool_call": 60,
            "over_explanation": 40,
        }
        assert [(p.phase, p.score, p.tokens) for p in report.phases] == [
            ("reasoning", 0.5, 180),
            ("tool_use", 0.8, 260),
            ("generation", 0.9, 160),
        ]
        assert [s.position for s in report.spans] == list(range(6))
        assert [s.aligned for s in report.spans] == [
            True,
            True,
            False,
            False,
            False,
            True,
        ]

    @pytest.mark.req("TER-RPT-001")
    def test_maps_economics_positional_and_uncertainty(self) -> None:
        report = from_ter_result(_ter_result())
        assert report.positional == PositionalTer(0.9, 0.6, 1.0, 2, 2, 2)
        assert report.economics is not None
        assert report.economics.cache_read_tokens == 3000
        assert report.economics.cost_usd == pytest.approx(0.05)
        u = report.uncertainty
        assert u is not None
        assert (u.lower, u.upper, u.reliability) == (0.6, 0.8, Reliability.MODERATE)
        assert u.width == pytest.approx(0.2)

    def test_optional_parts_are_none_when_absent(self) -> None:
        report = from_ter_result(
            _ter_result(economics=None, uncertainty=None, classified_spans=[]),
            sections=(ReportSection("a3", "A3", ("text",)),),
        )
        assert report.positional is None
        assert report.economics is None
        assert report.uncertainty is None
        assert report.spans == ()
        assert report.sections[0].key == "a3"


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


class TestPalette:
    def test_palette_matches_visualize_order(self) -> None:
        from ter_calculator.charts import PALETTE

        assert list(palette.PALETTE) == PALETTE
        assert palette.ROLES["aligned"].light == PALETTE[0]
        assert palette.ROLES["waste"].light == PALETTE[7]

    def test_resolve_roles_and_hex(self) -> None:
        assert palette.resolve("waste") == ("#e34948", "waste")
        assert palette.resolve("#2A78D6") == ("#2A78D6", "series-1")
        assert palette.resolve("#123456") == ("#123456", None)
        assert palette.on_colour("#123456") == ("#ffffff", None)
        assert palette.on_colour("series-4") == ("#0b0b0b", "on-series-4")

    def test_stylesheet_has_light_dark_and_print(self) -> None:
        css = palette.stylesheet(".x")
        assert "prefers-color-scheme: dark" in css
        assert "@media print" in css
        assert ".x .ter-f-waste{fill:var(--ter-waste)}" in css


# ---------------------------------------------------------------------------
# SVG structure
# ---------------------------------------------------------------------------


class TestSvgStructure:
    def test_every_report_chart_is_well_formed_and_labelled(self) -> None:
        charts = report_charts(from_ter_result(_ter_result()))
        assert set(charts) == {
            "key_metrics",
            "aligned_waste",
            "composition",
            "span_timeline",
            "phase_scores",
            "positional_ter",
            "waste_pareto",
            "economics",
        }
        for name, markup in charts.items():
            root = _parse_svg(markup)
            assert "http" not in (root.get("href") or ""), name

    def test_hostile_strings_are_escaped_in_every_chart(self) -> None:
        charts = report_charts(_hostile_report())
        for markup in charts.values():
            _parse_svg(markup)
            assert "<script" not in markup
            assert "-->" not in markup
            assert "\x01" not in markup
        assert "&lt;/script&gt;" in charts["waste_pareto"]

    def test_timeline_marks_waste_with_hatch_and_lanes_unknown_phases(self) -> None:
        spans = (
            SpanCell(0, "reasoning", "aligned_reasoning", 5, 0.9),
            SpanCell(1, "planning", "over_explanation", 5, 0.4),
        )
        root = _parse_svg(svg.span_timeline(spans))
        texts = [t.text for t in root.iter(f"{SVG_NS}text")]
        assert "Planning" in texts
        fills = [r.get("fill") for r in root.iter(f"{SVG_NS}rect")]
        assert fills.count("url(#span-timeline-hatch)") == 2  # legend + waste cell

    def test_pareto_cumulative_line_ends_at_full_share(self) -> None:
        markup = svg.waste_pareto([WasteByType("b", 25, 1), WasteByType("a", 75, 2)])
        root = _parse_svg(markup)
        titles = [t.text for t in root.iter(f"{SVG_NS}title")]
        assert "Cumulative 75.0%" in titles
        assert "Cumulative 100.0%" in titles
        assert titles.index("Cumulative 75.0%") < titles.index("Cumulative 100.0%")

    def test_positional_sparkline_without_reference_line(self) -> None:
        markup = svg.positional_sparkline(PositionalTer(1.0, 0.0, 0.5))
        _parse_svg(markup)
        assert "stroke-dasharray" not in markup

    def test_primitives_return_empty_for_no_data(self) -> None:
        assert svg.stat_tiles([]) == ""
        assert svg.stacked_bar("t", [("a", 0.0, "series-1")]) == ""
        assert svg.horizontal_bar("t", []) == ""
        assert svg.span_timeline(()) == ""
        assert svg.waste_pareto([]) == ""
        assert svg.waste_pareto([WasteByType("a", 0, 1)]) == ""

    def test_minimal_report_draws_only_what_it_has(self) -> None:
        charts = report_charts(_minimal_report())
        assert set(charts) == {"key_metrics"}

    def test_stacked_bar_legend_wraps(self) -> None:
        segments = [(f"segment number {i}", 10.0, f"series-{i + 1}") for i in range(8)]
        root = _parse_svg(svg.stacked_bar("Wrap", segments, width=400))
        assert int(root.get("height") or 0) > 150

    def test_formatters(self) -> None:
        assert svg.fmt_tokens(950) == "950"
        assert svg.fmt_tokens(1500) == "1.5k"
        assert svg.fmt_tokens(2_500_000) == "2.5M"
        assert svg.humanise("tool_use") == "Tool use"
        assert svg.humanise("") == ""
        assert svg.slug("!!!") == "chart"


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


class TestHtmlReport:
    @pytest.mark.req("TER-RPT-002")
    def test_report_is_self_contained(self) -> None:
        page = render_report_html(from_ter_result(_ter_result()))
        assert page.startswith("<!doctype html>")
        assert "<script" not in page
        assert not re.search(r"\s(src|href)=", page)
        assert set(re.findall(r"url\(([^)]*)\)", page)) <= {"#span-timeline-hatch"}
        urls = set(re.findall(r"https?://[^\s\"'<>]+", page))
        assert urls == {"http://www.w3.org/2000/svg"}
        assert "default-src 'none'" in page
        assert "prefers-color-scheme: dark" in page
        assert "@media print" in page
        assert "max-width:760px" in page

    def test_report_has_every_part(self) -> None:
        page = render_report_html(from_ter_result(_ter_result()))
        for marker in (
            'class="kpis"',
            'id="waste-patterns"',
            'id="uncertainty"',
            'id="how-to-read"',
            "Span timeline",
            "Waste patterns (Pareto)",
            "TER across the session",
        ):
            assert marker in page
        assert page.count("<svg") == 6

    def test_every_inline_svg_is_well_formed(self) -> None:
        page = render_report_html(_hostile_report())
        blocks = re.findall(r"<svg.*?</svg>", page, flags=re.S)
        assert blocks
        for block in blocks:
            _parse_svg(block)

    @pytest.mark.req("TER-RPT-002")
    def test_session_strings_are_escaped(self) -> None:
        page = render_report_html(_hostile_report())
        assert "<script" not in page
        assert "-->" not in page
        assert "<!--" not in page
        assert "\x01" not in page
        assert "&lt;/script&gt;&lt;script&gt;alert(1)" in page

    def test_minimal_report_renders_fallbacks(self) -> None:
        page = render_report_html(_minimal_report())
        assert "No waste patterns were detected" in page
        assert "no uncertainty estimate" in page
        assert page.count("<svg") == 0

    @pytest.mark.req("TER-RPT-002")
    def test_uncertainty_note(self) -> None:
        report = from_ter_result(_ter_result())
        note = uncertainty_note(0.7, report.uncertainty)
        assert "A 95% deterministic span bootstrap interval" in note
        assert "between 0.60 and 0.80" in note
        assert "60 tokens (10.0%) were classified with low confidence" in note
        assert "reliability is moderate" in note
        page = render_report_html(report)
        assert note in page
        assert "95% interval 0.60 to 0.80" in page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _Encoding:
    def encode(self, text: str) -> range:
        return range(RegexTokenizer().count(text))


@pytest.fixture
def offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_cache, "_TIKTOKEN_ENC", _Encoding())
    monkeypatch.setitem(
        embedding_cache._MODEL_CACHE,
        embedding_cache.DEFAULT_MODEL_NAME,
        HashingEmbedder(embedding_cache.EMBEDDING_DIM),
    )


@pytest.mark.usefixtures("offline_models")
class TestReportHtmlCommand:
    def test_writes_html_only(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "out" / "report.html"
        assert main(["report", str(SAMPLE), "--html", str(target)]) == 0
        captured = capsys.readouterr()
        assert "Wrote" in captured.err
        assert captured.out == ""
        page = target.read_text(encoding="utf-8")
        assert page.startswith("<!doctype html>")
        assert "sample-session-001" in page

    def test_writes_html_and_markdown(self, tmp_path: Path) -> None:
        html_path = tmp_path / "r.html"
        md_path = tmp_path / "r.md"
        code = main(
            [
                "--quiet",
                "report",
                str(SAMPLE),
                "--html",
                str(html_path),
                "-o",
                str(md_path),
            ]
        )
        assert code == 0
        assert html_path.exists()
        assert md_path.read_text(encoding="utf-8").strip()
