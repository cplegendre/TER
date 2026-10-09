"""WIP, the six scorecard dimensions, Software Value Efficiency and the Lean
concept map (TER-WIP-001, TER-SCR-001, TER-SCR-003, TER-LEN-005, TER-LEN-006)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from ter4_lean_builder import FAIL, PASS, Script

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import format_findings
from ter.adapters.driving.reports import render_a3_html
from ter.adapters.driving.cli import main
from ter.bootstrap import cli_services
from ter.domain import AnalysisEngine, EventKind, ToolCall, ToolKind
from ter.domain.lean import (
    DEFAULT_REGISTRY,
    LEAN_MEASURES,
    ActivityClass,
    Dimension,
    LeanAnalysis,
    LeanConcept,
    TerMeasure,
    ValueStatus,
    WipKind,
    WipTracker,
    build_a3,
    explain,
    software_value_efficiency,
)
from ter.domain.outcome import (
    CheckEvidence,
    CheckStatus,
    OutcomeEvidence,
    OutcomeVerdict,
    Verdict,
    judge,
)

ROOT = Path(__file__).resolve().parents[2]


def analysis_of(script: Script, **kw: Any) -> LeanAnalysis:
    return explain(script.events, RegexTokenizer(), **kw)


def counts(analysis: LeanAnalysis) -> list[tuple[int, int, int, int]]:
    return [(s.hypotheses, s.tasks, s.edits, s.failures) for s in analysis.wip.samples]


def verdict(*statuses: CheckStatus) -> OutcomeVerdict:
    return judge(
        OutcomeEvidence(
            "run",
            "test",
            tuple(CheckEvidence(f"c{i}", s, "r") for i, s in enumerate(statuses)),
        )
    )


ACCEPTED = (CheckStatus.PASSED,)
REJECTED = (CheckStatus.FAILED,)
INCOMPLETE = (CheckStatus.SKIPPED,)


# --- WIP ------------------------------------------------------------------------


@pytest.mark.req("TER-WIP-001")
def test_an_edit_is_open_until_a_later_validation_run_reports() -> None:
    s = Script()
    s.prompt("fix")
    s.edit("a.py")  # request, result
    s.bash("pytest -q", PASS)  # request, result
    a = analysis_of(s)
    assert [e for _, _, e, _ in counts(a)] == [0, 1, 1, 1, 0]
    assert a.wip.edits_opened == a.wip.edits_validated == 1
    assert a.wip.peak_of(WipKind.EDITS) == 1


@pytest.mark.req("TER-WIP-001")
def test_an_edit_after_the_validation_request_stays_open() -> None:
    # Boundary: a run only covers edits requested before it.
    s = Script()
    s.prompt("fix")
    run, _ = s.bash("pytest -q", None)
    edit, _ = s.edit("a.py", output=None)
    assert run.tool is not None
    s._add(
        EventKind.TOOL_COMPLETED,
        PASS,
        ToolCall("Bash", ToolKind.EXEC_SHELL, run.tool.call_id),
    )
    a = analysis_of(s)
    assert a.wip.still_open(WipKind.EDITS) == (edit.id,)
    assert a.wip.edits_validated == 0


@pytest.mark.req("TER-WIP-001")
def test_an_edit_without_any_validation_is_still_open_at_the_end() -> None:
    s = Script()
    s.prompt("fix")
    edit, _ = s.edit("a.py")
    s.bash("ls", "a.py")  # exploring shell validates nothing
    s.say("done")
    a = analysis_of(s)
    assert a.wip.still_open(WipKind.EDITS) == (edit.id,)


@pytest.mark.req("TER-WIP-001")
def test_a_failure_is_open_until_the_same_check_passes() -> None:
    s = Script()
    s.prompt("fix")
    _, first = s.bash("pytest -q", FAIL)
    s.edit("a.py")
    s.bash("pytest -q", FAIL)  # the same check failing again: still one
    s.bash("ruff check .", "All checks passed!")  # another check passing
    assert first is not None
    mid = analysis_of(s)
    assert mid.wip.still_open(WipKind.FAILURES) == (first.id,)
    assert mid.wip.peak_of(WipKind.FAILURES) == 1
    s.bash("pytest -q", PASS)
    done = analysis_of(s)
    assert done.wip.still_open(WipKind.FAILURES) == ()
    assert done.wip.final is not None and done.wip.final.failures == 0


@pytest.mark.req("TER-WIP-001")
def test_an_unknown_validation_outcome_opens_and_closes_nothing() -> None:
    s = Script()
    s.prompt("fix")
    s.bash("pytest -q", FAIL)
    s.bash("pytest -q", "collected 0 items")
    assert analysis_of(s).wip.peak_of(WipKind.FAILURES) == 1
    assert analysis_of(s).wip.final is not None
    assert analysis_of(s).wip.final.failures == 1  # type: ignore[union-attr]


@pytest.mark.req("TER-WIP-001")
def test_tasks_are_open_todo_items_and_unfinished_handoffs() -> None:
    s = Script()
    s.prompt("build it")
    todos = [
        {"content": "parser", "status": "in_progress"},
        {"content": "tests", "status": "pending"},
        {"content": "docs", "status": "completed"},
    ]
    s.call("TodoWrite", ToolKind.PLAN, {"todos": todos}, "ok")
    handoff, _ = s.task("survey", "find the parser", output=None)
    a = analysis_of(s)
    assert a.wip.final is not None and a.wip.final.tasks == 3
    assert handoff.id in a.wip.still_open(WipKind.TASKS)
    s.call(
        "TodoWrite",
        ToolKind.PLAN,
        {"todos": [{**t, "status": "completed"} for t in todos]},
        "ok",
    )
    after = analysis_of(s)
    assert after.wip.final is not None and after.wip.final.tasks == 1
    s2 = Script()
    s2.prompt("x")
    s2.task("survey", "find it")  # the result arrives: the handoff is done
    s2.todo("an item without a status is open")
    final = analysis_of(s2).wip.final
    assert final is not None and final.tasks == 1


@pytest.mark.req("TER-WIP-001")
def test_hypotheses_open_on_exploration_and_resolve_on_action_or_turn_end() -> None:
    s = Script()
    s.prompt("fix the parser")
    s.read("a.py")
    s.read("a.py")  # the same subject: no second hypothesis
    s.search("parse")
    s.bash("git status", "clean")
    mid = analysis_of(s)
    assert mid.wip.final is not None and mid.wip.final.hypotheses == 3
    s.edit("a.py")  # acted on: the hypothesis about a.py resolves
    acted = analysis_of(s)
    assert acted.wip.final is not None and acted.wip.final.hypotheses == 2
    s.say("Found it")  # narration does not end the turn
    assert analysis_of(s).wip.final.hypotheses == 2  # type: ignore[union-attr]
    s._add(EventKind.TASK_COMPLETED)  # the turn ended
    ended = analysis_of(s)
    assert ended.wip.final is not None and ended.wip.final.hypotheses == 0
    s.read("b.py")
    s.prompt("next")  # a new prompt ends the previous turn too
    assert analysis_of(s).wip.final.hypotheses == 0  # type: ignore[union-attr]


@pytest.mark.req("TER-WIP-001")
def test_wip_is_reported_after_every_event_with_its_peak_and_open_items() -> None:
    s = Script()
    s.prompt("fix")
    s.read("a.py")
    s.task("survey", "look", output=None)
    s.edit("b.py")
    s.bash("pytest -q", FAIL)
    s._add(EventKind.SUBAGENT_COMPLETED)
    a = analysis_of(s)
    assert len(a.wip.samples) == len(s.events)
    assert [x.event_id for x in a.wip.samples] == [e.id for e in s.events]
    assert a.wip.peak is not None
    assert a.wip.peak.total == max(x.total for x in a.wip.samples)
    first_peak = next(x for x in a.wip.samples if x.total == a.wip.peak.total)
    assert a.wip.peak == first_peak
    for kind in WipKind:
        assert a.wip.peak_of(kind) == max(x.count(kind) for x in a.wip.samples)
    ids = {e.id for e in s.events}
    assert all(set(i) <= ids for _, i in a.wip.open_at_end)
    d = a.as_dict()["wip"]
    assert isinstance(d, dict)
    assert d["kinds"] == ["hypotheses", "tasks", "edits", "failures"]
    assert len(d["series"]) == len(s.events)
    assert d["peak"]["total"] == a.wip.peak.total


@pytest.mark.req("TER-WIP-001", "TER-ANL-010")
def test_wip_live_equals_batch_and_ignores_redelivery() -> None:
    s = Script()
    s.prompt("fix")
    s.read("a.py")
    s.edit("a.py")
    s.bash("pytest -q", FAIL)
    s.edit("a.py", "2")
    s.bash("pytest -q", PASS)
    s.say("done")
    engine = AnalysisEngine(RegexTokenizer())
    batch = analysis_of(s).wip
    for i, event in enumerate(s.events):
        engine.apply(event)
        engine.apply(event)  # a redelivery changes nothing
        live = engine.explain().wip
        assert live.samples == batch.samples[: i + 1]
    assert engine.explain().wip == batch
    # Recounting from steps alone agrees when no to-do list or lifecycle event
    # is involved.
    assert WipTracker.of_steps(engine.explain().steps) == batch


@pytest.mark.req("TER-WIP-001")
def test_the_a3_shows_wip_over_the_session_and_its_peak() -> None:
    s = Script()
    s.prompt("fix")
    s.read("a.py")
    s.edit("a.py")
    s.edit("b.py")
    s.bash("pytest -q", FAIL)
    report = build_a3(analysis_of(s), ["fix"])
    d = report.as_dict()
    wip = d["analysis"]["wip"]  # type: ignore[index]
    assert len(wip["series"]) == len(s.events)
    assert wip["peak"]["total"] == report.analysis.wip.peak.total  # type: ignore[union-attr]
    page = render_a3_html(report)
    assert f"Work in progress (peak {wip['peak']['total']})" in page
    assert "Peak WIP" in page
    text = format_findings(report.analysis, report.value_efficiency)
    assert f"WIP              peak {wip['peak']['total']}" in text


@pytest.mark.req("TER-WIP-001")
def test_an_empty_session_has_no_wip_peak() -> None:
    a = analysis_of(Script())
    assert a.wip.peak is None and a.wip.final is None
    assert a.as_dict()["wip"]["peak"] is None  # type: ignore[index]
    assert "Work in progress" not in render_a3_html(build_a3(a))


# --- scorecard dimensions -------------------------------------------------------


def _session() -> Script:
    s = Script()
    s.prompt("Fix the parser and keep tests green")
    s.read("src/a.py", "def parse(): pass")
    s.bash("pytest -q", FAIL)
    s.edit("src/a.py", "1")
    s.bash("pytest -q", FAIL)
    s.edit("src/a.py", "2")
    s.bash("pytest -q", PASS)
    s.edit("src/b.py", "3")
    s.say("Done")
    return s


@pytest.mark.req("TER-SCR-001")
def test_the_scorecard_has_six_separate_dimensions() -> None:
    a = analysis_of(_session(), ter=TerMeasure(0.6, "t"))
    report = build_a3(a, ["fix"])
    dims = report.dimensions
    assert [d.dimension for d in dims] == list(Dimension)
    assert [d.value for d in Dimension] == [
        "efficiency",
        "flow",
        "quality",
        "cost",
        "risk",
        "outcome",
    ]
    by = {d.dimension: d for d in dims}
    sc = a.scorecard
    # Each dimension holds its own measures; none is folded into another.
    keys = [m.key for d in dims for m in d.measures]
    assert len(keys) == len(set(keys))
    assert by[Dimension.EFFICIENCY].measure("ter").value == 0.6
    assert by[Dimension.FLOW].measure("flow_efficiency_tokens").value == (
        sc.flow_efficiency_tokens
    )
    assert by[Dimension.FLOW].measure("wip_peak").value == a.wip.peak.total  # type: ignore[union-attr]
    quality = by[Dimension.QUALITY]
    assert quality.measure("validation_runs").value == 3
    assert quality.measure("validations_failed").value == 2
    assert quality.measure("validations_passed").value == 1
    assert quality.measure("rework_cycles").value == sc.rework_cycles == 1
    assert quality.measure("iterations").value == sc.iterations == 1
    assert quality.measure("edits_validated_share").value == pytest.approx(2 / 3)
    cost = by[Dimension.COST]
    assert cost.measure("generated_tokens").value == sc.generated_tokens
    assert cost.measure("waste_tokens").value == sc.waste_tokens
    risk = by[Dimension.RISK]
    assert risk.measure("risk_findings").value == sc.risks
    assert risk.measure("unvalidated_edits_at_end").value == 1  # src/b.py
    assert risk.measure("unresolved_failures_at_end").value == 0
    outcome = by[Dimension.OUTCOME]
    assert outcome.measure("verdict").value == "unknown"
    d = report.as_dict()["analysis"]["dimensions"]  # type: ignore[index]
    assert [x["dimension"] for x in d] == [x.value for x in Dimension]
    page = render_a3_html(report)
    assert "Scorecard dimensions" in page
    for dim in Dimension:
        assert f'<th scope="row">{dim.label}' in page


@pytest.mark.req("TER-SCR-001")
def test_the_outcome_dimension_shows_the_verdict_when_supplied() -> None:
    a = analysis_of(_session())
    judged = build_a3(a, ["fix"], verdict(*REJECTED))
    by = {d.dimension: d for d in judged.dimensions}
    assert by[Dimension.OUTCOME].measure("verdict").value == "rejected"
    plain = {d.dimension: d for d in build_a3(a, ["fix"]).dimensions}
    for dim in (Dimension.FLOW, Dimension.QUALITY, Dimension.COST, Dimension.RISK):
        assert by[dim] == plain[dim]


# --- Software Value Efficiency --------------------------------------------------


@pytest.mark.req("TER-SCR-003")
@pytest.mark.parametrize(
    ("statuses", "status", "has_value"),
    [
        (None, ValueStatus.UNKNOWN, False),
        (INCOMPLETE, ValueStatus.UNKNOWN, False),
        (REJECTED, ValueStatus.NO_VALUE, True),
        (ACCEPTED, ValueStatus.MEASURED, True),
    ],
)
def test_software_value_efficiency_sits_next_to_ter(
    statuses: tuple[CheckStatus, ...] | None, status: ValueStatus, has_value: bool
) -> None:
    a = analysis_of(_session(), ter=TerMeasure(0.6, "t"))
    report = build_a3(a, ["fix"], None if statuses is None else verdict(*statuses))
    sve = report.value_efficiency
    assert sve.status is status
    assert (sve.value is not None) is has_value
    # JSON: SVE is the key right after TER in the scorecard.
    keys = list(report.as_dict()["analysis"]["scorecard"])  # type: ignore[index]
    assert keys[keys.index("ter") + 1] == "software_value_efficiency"
    # HTML: the SVE tile directly follows the TER tile.
    page = render_a3_html(report)
    ter_at = page.index("<span>TER</span>")
    sve_at = page.index("<span>Software Value Efficiency</span>")
    assert 0 < sve_at - ter_at
    assert "<span>" not in page[ter_at + len("<span>TER</span>") : sve_at]
    # Text: the value-efficiency line directly follows the TER line.
    lines = format_findings(a, sve).splitlines()
    ter_line = next(i for i, x in enumerate(lines) if x.startswith("  TER"))
    assert lines[ter_line + 1].startswith("  value efficiency")
    efficiency = report.dimensions[0]
    assert [m.key for m in efficiency.measures][:2] == [
        "ter",
        "software_value_efficiency",
    ]


@pytest.mark.req("TER-SCR-003")
def test_software_value_efficiency_definition() -> None:
    a = analysis_of(_session())
    sc = a.scorecard
    value_tokens = dict(sc.activity_tokens)[ActivityClass.VALUE_ADDING.value]
    value_seconds = dict(sc.activity_seconds)[ActivityClass.VALUE_ADDING.value]
    accepted = software_value_efficiency(sc, verdict(*ACCEPTED))
    assert accepted.verdict is Verdict.ACCEPTED and accepted.verified_outcomes == 1
    assert accepted.tokens == pytest.approx(value_tokens / sc.generated_tokens)
    assert accepted.time == pytest.approx(value_seconds / sc.agent_seconds)
    assert accepted.tokens_per_outcome == sc.generated_tokens
    assert accepted.seconds_per_outcome == sc.agent_seconds
    rejected = software_value_efficiency(sc, verdict(*REJECTED))
    assert rejected.tokens == rejected.time == 0.0
    assert rejected.verified_outcomes == 0 and rejected.tokens_per_outcome is None
    for unknown in (
        software_value_efficiency(sc, None),
        software_value_efficiency(sc, verdict(*INCOMPLETE)),
    ):
        assert unknown.tokens is unknown.time is unknown.verified_outcomes is None
        assert (
            "Unknown" in unknown.reason
            and "never inferred from tokens" in unknown.reason
        )
    d = accepted.as_dict()
    assert d["status"] == "measured" and "Software Value Efficiency" in str(
        d["definition"]
    )
    empty = software_value_efficiency(
        analysis_of(Script()).scorecard, verdict(*ACCEPTED)
    )
    assert empty.tokens is None and empty.time is None
    untimed = Script(timed=False)
    untimed.prompt("x")
    untimed.edit("a.py")
    untimed.say("y")
    sve = software_value_efficiency(analysis_of(untimed).scorecard, verdict(*ACCEPTED))
    assert (
        sve.tokens is not None and sve.time is None and sve.seconds_per_outcome is None
    )


@pytest.mark.req("TER-SCR-003")
def test_explain_json_and_text_carry_software_value_efficiency() -> None:
    session = ROOT / "sample_sessions" / "example_session.jsonl"
    out, err = io.StringIO(), io.StringIO()
    argv = ["explain", str(session), "--json"]
    assert main(argv, cli_services(), stdout=out, stderr=err) == 0
    sc = json.loads(out.getvalue())["scorecard"]
    assert sc["software_value_efficiency"]["status"] == "unknown"
    out = io.StringIO()
    assert main(argv[:2], cli_services(), stdout=out, stderr=err) == 0
    assert "value efficiency unknown" in out.getvalue()


# --- value per unit of resource, never token count alone -------------------------


class _Scaled:
    """A tokenizer that counts every text ``factor`` times."""

    def __init__(self, factor: int) -> None:
        self._inner = RegexTokenizer()
        self._factor = factor

    def count(self, text: str) -> int:
        return self._inner.count(text) * self._factor


def _judgements(a: LeanAnalysis, outcome: OutcomeVerdict | None) -> dict[str, Any]:
    report = build_a3(a, ["fix"], outcome)
    sc = a.scorecard
    sve = report.value_efficiency
    return {
        "flow_tokens": sc.flow_efficiency_tokens,
        "flow_time": sc.flow_efficiency_time,
        "shares": {k: sc.activity_share(k) for k, _ in sc.activity_tokens},
        "composite": None if sc.composite is None else sc.composite.value,
        "sve_tokens": sve.tokens,
        "sve_time": sve.time,
        "sve_status": sve.status,
        "verdict": None if outcome is None else outcome.verdict,
        "findings": [(f.detector, f.confidence) for f in a.findings],
    }


@pytest.mark.req("TER-LEN-005")
@pytest.mark.parametrize("statuses", [None, ACCEPTED, REJECTED])
def test_cutting_tokens_at_constant_value_changes_no_efficiency_judgement(
    statuses: tuple[CheckStatus, ...] | None,
) -> None:
    events = _session().events
    outcome = None if statuses is None else verdict(*statuses)
    ter = TerMeasure(0.5, "t")
    many = explain(events, _Scaled(3), ter=ter)
    few = explain(events, _Scaled(1), ter=ter)
    assert few.scorecard.generated_tokens * 3 == many.scorecard.generated_tokens
    assert few.scorecard.waste_tokens < many.scorecard.waste_tokens
    a, b = _judgements(few, outcome), _judgements(many, outcome)
    for key in ("flow_tokens", "flow_time", "composite", "sve_tokens", "sve_time"):
        assert a[key] == pytest.approx(b[key]) if a[key] is not None else b[key] is None
    assert a["shares"] == pytest.approx(b["shares"])
    assert (a["sve_status"], a["verdict"], a["findings"]) == (
        b["sve_status"],
        b["verdict"],
        b["findings"],
    )


@pytest.mark.req("TER-LEN-005")
def test_fewer_tokens_without_value_are_not_more_efficient() -> None:
    big = _session()
    big.read("docs/README.md", "words " * 200)
    small = Script()
    small.prompt("Fix the parser")
    small.say("I could not do it.")
    large_a, small_a = analysis_of(big), analysis_of(small)
    assert small_a.scorecard.generated_tokens < large_a.scorecard.generated_tokens
    delivered = build_a3(large_a, ["fix"], verdict(*ACCEPTED)).value_efficiency
    failed = build_a3(small_a, ["fix"], verdict(*REJECTED)).value_efficiency
    assert delivered.value is not None and failed.value is not None
    assert failed.value < delivered.value
    # And with no evidence either way, neither session's value is guessed.
    assert build_a3(small_a).value_efficiency.value is None
    assert build_a3(large_a).value_efficiency.value is None


@pytest.mark.req("TER-LEN-005")
def test_the_report_names_token_minimisation_as_a_non_goal() -> None:
    page = render_a3_html(build_a3(analysis_of(_session()), ["fix"]))
    assert "Token minimisation is not a goal" in page
    assert "value delivered per unit of resource" in page


# --- Lean concepts and their measures ------------------------------------------


def _rich() -> Script:
    s = _session()
    s.call("TodoWrite", ToolKind.PLAN, {"todos": [{"content": "x"}]}, "ok")
    s.task("survey", "find", output=None)
    return s


def _resolve(data: object, path: str) -> object:
    for part in path.split("."):
        assert isinstance(data, dict) and part in data, path
        data = data[part]
    return data


@pytest.mark.req("TER-LEN-006")
def test_every_lean_concept_maps_to_measures_the_code_computes() -> None:
    names = "value, flow, pull, WIP, queues, rework, defects, waiting, over-processing, motion"
    assert [c.label.lower() for c in LeanConcept] == names.lower().split(", ")
    assert set(LEAN_MEASURES) == set(LeanConcept)
    detectors = {d.id for d in DEFAULT_REGISTRY}
    report = build_a3(analysis_of(_rich()), ["fix"], verdict(*ACCEPTED))
    data = json.loads(json.dumps(report.as_dict()))
    for concept, measures in LEAN_MEASURES.items():
        assert measures, concept
        for m in measures:
            assert m.name and m.meaning
            if m.is_detector:
                assert m.target in detectors, m.source
            else:
                assert m.source.startswith("a3:"), m.source
                assert _resolve(data, m.target) is not None, m.source
    listed = data["lean_concepts"]
    assert [x["concept"] for x in listed] == [c.value for c in LeanConcept]


@pytest.mark.req("TER-LEN-006")
def test_the_lean_concept_map_is_in_the_a3_and_the_docs() -> None:
    page = render_a3_html(build_a3(analysis_of(_rich()), ["fix"]))
    docs = (ROOT / "docs" / "ter4" / "l2-explained.md").read_text(encoding="utf-8")
    section = docs.split("## Lean concepts and their measures", 1)[1].split("\n## ", 1)[
        0
    ]
    for concept, measures in LEAN_MEASURES.items():
        assert f'<th scope="row">{concept.label}</th>' in page
        row = next(
            line
            for line in section.splitlines()
            if line.startswith(f"| {concept.label} |")
        )
        for m in measures:
            assert f"<code>{m.source}</code>" in page
            assert f"`{m.source}`" in row, (concept, m.source)
