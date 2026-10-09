"""The GARE session source: exported GARE runs as ter.event traces (issue #52)."""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log.codec import event_from_record, event_to_record
from ter.adapters.driven.gare import (
    GARE_USAGE_SCHEMA,
    NO_CACHE_TOKENS,
    NO_RESPONSE_TEXT,
    GareRunSource,
)
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import format_report, main
from ter.adapters.driving.reports.a3 import render_a3_html
from ter.application.observe import AnalyseTrace
from ter.bootstrap import cli_services, session_source_for
from ter.domain import Actor, EventKind
from ter.domain.events import EVENT_SCHEMA_VERSION, READABLE_SCHEMA_VERSIONS
from ter.domain.stream import EventClass
from tests.golden.corpus import CORPUS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gare"
FAILOVER = FIXTURES / "failover-run"
MISSION = FIXTURES / "repair-mission"
#: A real recorded run (issue #55): GARE 0.0.50 with a local Ollama model.
REAL = FIXTURES / "runs" / "c2ffdf8f1b09"
ROUTING = (
    EventKind.ROUTE_SELECTED,
    EventKind.ROUTE_FAILOVER,
    EventKind.ATTEMPT_STARTED,
    EventKind.VERIFICATION_COMPLETED,
    EventKind.OUTCOME_RECORDED,
)


def kinds(path: Path) -> Counter[EventKind]:
    return Counter(e.kind for e in GareRunSource().read(path).events)


def usage_rows(path: Path) -> list[dict[str, Any]]:
    lines = (path / "gare-ter-usage.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def explain(*events: tuple[str, dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "run": {"id": "run1", "goal": "fix it", "status": "succeeded"},
        "tasks": [],
        "errors": [],
        "events": [
            {
                "state": state,
                "created_at": f"2026-10-08T12:00:{i:02d}+00:00",
                "detail": d,
            }
            for i, (state, d) in enumerate(events)
        ],
        **extra,
    }


def write_explain(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "explain.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -- the contract ------------------------------------------------------------


@pytest.mark.req("TER-EVT-001")
def test_the_contract_defines_the_routing_kinds_as_unscored_lifecycle() -> None:
    assert EVENT_SCHEMA_VERSION == "ter.event/0.4"  # 0.3 added the routing kinds
    assert {
        "ter.event/0.1",
        "ter.event/0.2",
        "ter.event/0.3",
    } < READABLE_SCHEMA_VERSIONS
    assert [k.value for k in ROUTING] == [
        "route.selected",
        "route.failover",
        "attempt.started",
        "verification.completed",
        "outcome.recorded",
    ]
    for kind in ROUTING:
        assert kind.is_lifecycle and not kind.is_generated
        assert EventClass.of(kind) is EventClass.LIFECYCLE


@pytest.mark.req("TER-EVT-001")
def test_routing_events_round_trip_through_the_event_log() -> None:
    for event in GareRunSource().read(FAILOVER).events:
        assert event_from_record(event_to_record(event)) == event


# -- mapping -----------------------------------------------------------------


@pytest.mark.req("TER-SRC-011")
def test_a_failover_run_maps_every_route_attempt_and_failover() -> None:
    trace = GareRunSource().read(FAILOVER)
    assert trace.session_id == "412da6a027da"
    assert trace.source_format == "gare-run"
    assert kinds(FAILOVER) == {
        EventKind.PROMPT: 1,
        EventKind.ATTEMPT_STARTED: 9,
        EventKind.ROUTE_SELECTED: 9,
        EventKind.ROUTE_FAILOVER: 3,
        EventKind.RESPONSE: 9,
        EventKind.TASK_COMPLETED: 1,
    }
    failovers = [e for e in trace.events if e.kind is EventKind.ROUTE_FAILOVER]
    assert all(
        "flaky/flaky-large failed (PROVIDER_UNAVAILABLE)" in e.text for e in failovers
    )
    assert all(e.actor is Actor.SYSTEM and e.usage is None for e in failovers)
    assert trace.events[0].kind is EventKind.PROMPT
    assert trace.events[0].text == "Add retry with backoff to the provider client"
    assert trace.coverage == 1.0


@pytest.mark.req("TER-SRC-011")
@pytest.mark.parametrize(
    "run", [FAILOVER, MISSION, REAL], ids=["failover", "mission", "real"]
)
def test_token_totals_equal_the_gare_export(run: Path) -> None:
    trace = GareRunSource().read(run)
    rows = usage_rows(run)
    usage = [e.usage for e in trace.events if e.usage is not None]
    assert sum(u.input_tokens for u in usage) == sum(r["input_tokens"] for r in rows)
    assert sum(u.output_tokens for u in usage) == sum(r["output_tokens"] for r in rows)


@pytest.mark.req("TER-SRC-011")
def test_a_mission_maps_its_attempt_and_its_outcome() -> None:
    trace = GareRunSource().read(MISSION)
    assert kinds(MISSION) == {
        EventKind.PROMPT: 1,
        EventKind.ROUTE_SELECTED: 2,
        EventKind.ATTEMPT_STARTED: 1,
        EventKind.RESPONSE: 2,
        EventKind.OUTCOME_RECORDED: 1,
        EventKind.TASK_COMPLETED: 1,
    }
    (attempt,) = [e for e in trace.events if e.kind is EventKind.ATTEMPT_STARTED]
    assert attempt.text.startswith("attempt 1 mission-coder-1-")
    (outcome,) = [e for e in trace.events if e.kind is EventKind.OUTCOME_RECORDED]
    assert outcome.text == "blocked: score 5/100"
    # Planning, worktree and diagnosis states are known, so nothing is lost.
    assert trace.unrecognised == ()


@pytest.mark.req("TER-SRC-011")
def test_each_repair_attempt_starts_once(tmp_path: Path) -> None:
    def route(task: str) -> tuple[str, dict[str, Any]]:
        return "route_decision", {
            "task_id": task,
            "candidates": [{"provider": "p", "model": "m", "score": 1.0}],
        }

    data = explain(
        ("created", {"goal": "fix it"}),
        route("mission-coder-1-aaaa1111"),
        route("mission-investigate-1-bbbb2222"),
        route("mission-coder-2-cccc3333"),
        route("mission-coder-2-cccc3333"),
    )
    trace = GareRunSource().read(write_explain(tmp_path, data))
    attempts = [e.text for e in trace.events if e.kind is EventKind.ATTEMPT_STARTED]
    assert attempts == [
        "attempt 1 mission-coder-1-aaaa1111",
        "attempt 2 mission-coder-2-cccc3333",
    ]


@pytest.mark.req("TER-SRC-011")
@pytest.mark.parametrize(
    ("state", "detail", "text"),
    [
        (
            "acceptance_evaluated",
            {"attempt": 1, "passed": True},
            "attempt 1 acceptance_evaluated: pass",
        ),
        (
            "acceptance_evaluated",
            {"attempt": 2, "passed": False},
            "attempt 2 acceptance_evaluated: fail",
        ),
        (
            "attempt_complete",
            {"attempt": 1, "test_exit_code": 0, "review_pass": True},
            "attempt 1 attempt_complete: pass",
        ),
        (
            "attempt_complete",
            {"attempt": 1, "test_exit_code": 1, "review_pass": True},
            "attempt 1 attempt_complete: fail",
        ),
        ("runtime_qa", {"attempt": 1, "passed": True}, "attempt 1 runtime_qa: pass"),
        ("browser_qa", {"attempt": 1}, "attempt 1 browser_qa: unknown"),
        (
            "persona_review",
            {"attempt": 1, "verdict": "revise"},
            "attempt 1 persona_review: fail",
        ),
        ("persona_review", {"verdict": "unknown"}, "persona_review: unknown"),
    ],
)
def test_verification_steps_say_whether_they_passed(
    tmp_path: Path, state: str, detail: dict[str, Any], text: str
) -> None:
    trace = GareRunSource().read(write_explain(tmp_path, explain((state, detail))))
    (event,) = trace.events
    assert event.kind is EventKind.VERIFICATION_COMPLETED
    assert event.text == text


def test_a_final_state_without_a_score_only_completes(tmp_path: Path) -> None:
    trace = GareRunSource().read(
        write_explain(tmp_path, explain(("succeeded", {"task_count": 3})))
    )
    assert [e.kind for e in trace.events] == [EventKind.TASK_COMPLETED]


@pytest.mark.req("TER-SRC-012")
def test_unknown_states_and_schemas_count_against_coverage(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    write_explain(run, explain(("created", {}), ("teleported", {}), ("planned", {})))
    rows = [
        {
            "schema": GARE_USAGE_SCHEMA,
            "run_id": "run1",
            "task_id": "t",
            "provider": "p",
            "model": "m",
            "input_tokens": 3,
            "output_tokens": 4,
            "success": 1,
            "created_at": "2026-10-08T12:00:05+00:00",
        },
        {"schema": "gare.ter.usage.v3", "run_id": "run1"},
    ]
    (run / "usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows) + "not json\n", encoding="utf-8"
    )
    trace = GareRunSource().read(run)
    assert trace.unrecognised_by_type == {
        "run_event:teleported": 1,
        "gare.ter.usage.v3": 1,
        "invalid-json": 1,
    }
    assert trace.coverage == pytest.approx(2 / 5)


# -- usage limits --------------------------------------------------------------


@pytest.mark.req("TER-SRC-013")
@pytest.mark.parametrize(
    "run", [FAILOVER, MISSION, REAL], ids=["failover", "mission", "real"]
)
def test_gare_traces_carry_no_cache_tokens_and_say_so(run: Path) -> None:
    trace = GareRunSource().read(run)
    assert NO_CACHE_TOKENS in trace.usage_limits
    for event in trace.events:
        if event.usage is not None:
            assert event.usage.cache_creation_tokens == 0
            assert event.usage.cache_read_tokens == 0


@pytest.mark.req("TER-SRC-014")
def test_reports_state_the_usage_limit_beside_their_token_figures() -> None:
    report = AnalyseTrace(GareRunSource(), RegexTokenizer())(FAILOVER)
    assert report.usage_limits == (NO_CACHE_TOKENS, NO_RESPONSE_TEXT)
    assert report.as_dict()["usage_limits"] == [NO_CACHE_TOKENS, NO_RESPONSE_TEXT]
    lines = format_report(report).splitlines()
    usage_line = next(line for line in lines if line.lstrip().startswith("usage"))
    text_line = next(line for line in lines if "text tokens" in line)
    assert "reports no cache tokens" in usage_line
    assert "no response text" not in usage_line
    # The text limit qualifies the counts taken from event text.
    assert "no response text" in text_line
    assert "no cache tokens" not in text_line


@pytest.mark.req("TER-SRC-014")
def test_reports_without_limits_are_unchanged() -> None:
    report = AnalyseTrace(ClaudeCodeJsonlSource(), RegexTokenizer())(
        CORPUS["example_session"]
    )
    assert report.usage_limits == ()
    assert "usage_limits" not in report.as_dict()
    assert "no cache tokens" not in format_report(report)


# -- references ----------------------------------------------------------------


def test_the_usage_export_alone_is_a_run(tmp_path: Path) -> None:
    trace = GareRunSource().read(FAILOVER / "gare-ter-usage.jsonl")
    assert trace.session_id == "412da6a027da"
    assert {e.kind for e in trace.events} == {
        EventKind.RESPONSE,
        EventKind.ROUTE_FAILOVER,
    }


def test_explain_alone_uses_each_tasks_final_route() -> None:
    trace = GareRunSource().read(FAILOVER / "explain.json")
    responses = [e for e in trace.events if e.kind is EventKind.RESPONSE]
    assert len(responses) == 9
    assert all(e.usage is not None and e.usage.total > 0 for e in responses)


def test_a_usage_export_of_several_runs_needs_one_run(tmp_path: Path) -> None:
    rows = [
        {
            "schema": GARE_USAGE_SCHEMA,
            "run_id": run,
            "task_id": "t",
            "provider": "p",
            "model": "m",
            "input_tokens": 1,
            "output_tokens": 1,
            "success": 1,
            "created_at": "2026-10-08T12:00:00+00:00",
        }
        for run in ("a", "b")
    ]
    path = tmp_path / "all.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="export one with"):
        GareRunSource().read(path)


def test_a_folder_without_gare_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="holds neither"):
        GareRunSource().read(tmp_path)


@pytest.mark.req("TER-SRC-004")
def test_rereading_gives_the_same_ids_and_ids_are_unique() -> None:
    first, second = GareRunSource().read(FAILOVER), GareRunSource().read(FAILOVER)
    assert first == second
    ids = [e.id for e in first.events]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (FAILOVER, True),
        (FAILOVER / "gare-ter-usage.jsonl", True),
        (FAILOVER / "explain.json", True),
        (CORPUS["example_session"], False),
        (FIXTURES / "README.md", False),
    ],
)
def test_the_composition_root_picks_the_source_by_reference(
    ref: Path, expected: bool
) -> None:
    assert GareRunSource.accepts(ref) is expected
    picked = session_source_for(ref)
    assert isinstance(picked, GareRunSource if expected else ClaudeCodeJsonlSource)


def test_observe_prints_a_gare_timeline_with_the_cache_caveat() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["observe", str(MISSION), "--timeline"], cli_services(), stdout=out, stderr=err
    )
    text = out.getvalue()
    assert (code, err.getvalue()) == (0, "")
    assert "session 78cad60f1908" in text
    assert "reports no cache tokens" in text
    for kind in ("route.selected", "attempt.started", "outcome.recorded"):
        assert kind in text
    # The kind column fits the longest routing kind.
    timeline = text.split("Timeline\n", 1)[1].splitlines()
    header, rows = timeline[0], timeline[1:]
    row = next(line for line in rows if "outcome.recorded" in line)
    assert row.index("system") == header.index("actor")


# -- no response text (review of PR #60) -------------------------------------


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.req("TER-SRC-016")
@pytest.mark.parametrize(
    "ref", [FAILOVER, FAILOVER / "explain.json"], ids=["usage", "explain-only"]
)
def test_gare_responses_carry_no_text_and_say_so(ref: Path) -> None:
    trace = GareRunSource().read(ref)
    assert NO_RESPONSE_TEXT in trace.usage_limits
    responses = [e for e in trace.events if e.kind is EventKind.RESPONSE]
    # The text is only the task and route; the tokens are on the usage figures.
    assert responses
    assert all(re.fullmatch(r"[\w-]+: [\w.-]+/[\w.:-]+", e.text) for e in responses)
    assert sum(e.usage.output_tokens for e in responses if e.usage) > 0


@pytest.mark.req("TER-SRC-016")
def test_explanations_and_a3s_state_the_sources_limits(tmp_path: Path) -> None:
    code, out, _ = run_cli("explain", str(FAILOVER))
    assert code == 0
    assert "no response text" in out and "no cache tokens" in out

    report = tmp_path / "a3.json"
    html = tmp_path / "a3.html"
    code, _, _ = run_cli(
        "a3", str(FAILOVER), "--json", str(report), "--html", str(html)
    )
    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["usage_limits"] == [NO_CACHE_TOKENS, NO_RESPONSE_TEXT]
    assert "no response text" in html.read_text(encoding="utf-8")


@pytest.mark.req("TER-SRC-016")
def test_claude_code_explanations_carry_no_limits(tmp_path: Path) -> None:
    services = cli_services()
    assert services.explain_transcript is not None
    explained = services.explain_transcript(
        CORPUS["example_session"], "regex", "off", None
    )
    assert explained.a3.usage_limits == ()
    assert "usage_limits" not in explained.a3.as_dict()
    assert "Limit." not in render_a3_html(explained.a3)
    code, out, _ = run_cli("explain", str(CORPUS["example_session"]))
    assert code == 0 and "  limit " not in out


@pytest.mark.req("TER-SRC-017")
@pytest.mark.parametrize(
    "ref",
    [MISSION, MISSION / "gare-ter-usage.jsonl", MISSION / "explain.json"],
    ids=["folder", "usage", "explain"],
)
def test_a3_on_a_gare_run_reports_without_a_ter_score(
    ref: Path, tmp_path: Path
) -> None:
    report = tmp_path / "a3.json"
    code, _, err = run_cli("a3", str(ref), "--json", str(report))
    assert code == 0
    assert "No TER score: TER 3 scoring reads Claude Code transcripts" in err
    scorecard = json.loads(report.read_text(encoding="utf-8"))["analysis"]["scorecard"]
    assert scorecard.get("ter") is None


@pytest.mark.req("TER-SRC-017")
def test_claude_code_sessions_still_get_a_ter_score() -> None:
    services = cli_services()
    assert services.explain_transcript is not None
    explained = services.explain_transcript(
        CORPUS["example_session"], "regex", "offline", None
    )
    assert explained.analysis.scorecard.ter is not None


# -- review of PR #60: discovery, broken files, order, other runs ------------


def usage_row(**extra: Any) -> dict[str, Any]:
    return {
        "schema": GARE_USAGE_SCHEMA,
        "run_id": "run1",
        "task_id": "t",
        "provider": "p",
        "model": "m",
        "input_tokens": 3,
        "output_tokens": 4,
        "success": 1,
        "created_at": "2026-10-08T12:00:05+00:00",
        **extra,
    }


def write_usage(path: Path, *rows: dict[str, Any] | str) -> Path:
    path.write_text(
        "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


@pytest.mark.req("TER-SRC-012")
@pytest.mark.parametrize(
    "first", ['{"schema": "gare.ter.usage.v3"}', "not json"], ids=["schema", "json"]
)
def test_a_leading_unknown_row_does_not_hide_the_export(
    tmp_path: Path, first: str
) -> None:
    write_usage(tmp_path / "usage.jsonl", first, usage_row())
    assert GareRunSource.accepts(tmp_path)
    assert GareRunSource.accepts(tmp_path / "usage.jsonl")
    trace = GareRunSource().read(tmp_path)
    assert [e.kind for e in trace.events] == [EventKind.RESPONSE]
    assert len(trace.unrecognised) == 1
    assert trace.coverage == pytest.approx(1 / 2)


def test_a_file_that_only_mentions_the_schema_is_not_an_export(
    tmp_path: Path,
) -> None:
    path = write_usage(
        tmp_path / "chat.jsonl", {"type": "user", "text": GARE_USAGE_SCHEMA}
    )
    assert not GareRunSource.accepts(path)


@pytest.mark.parametrize(
    "content", ["{not json", '{"run": {"id": "run1"}}'], ids=["json", "shape"]
)
def test_a_broken_explanation_beside_usage_is_refused(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "explain.json").write_text(content, encoding="utf-8")
    write_usage(tmp_path / "usage.jsonl", usage_row())
    with pytest.raises(ValueError, match="is not `gare explain --json` output"):
        GareRunSource().read(tmp_path)


def test_an_explanation_without_a_run_id_is_refused(tmp_path: Path) -> None:
    data = explain(("created", {}))
    data["run"]["id"] = ""
    with pytest.raises(ValueError, match="names no run id"):
        GareRunSource().read(write_explain(tmp_path, data))


@pytest.mark.req("TER-SRC-012")
def test_usage_rows_of_another_run_count_against_coverage(tmp_path: Path) -> None:
    write_explain(tmp_path, explain(("created", {"goal": "fix it"})))
    write_usage(tmp_path / "usage.jsonl", usage_row(), usage_row(run_id="other"))
    trace = GareRunSource().read(tmp_path)
    assert trace.unrecognised_by_type == {"other-run:other": 1}
    assert [e.kind for e in trace.events] == [EventKind.PROMPT, EventKind.RESPONSE]


@pytest.mark.req("TER-SRC-012")
def test_only_rows_of_another_run_fall_back_to_the_tasks(tmp_path: Path) -> None:
    data = explain(
        ("created", {}),
        tasks=[{"id": "t", "provider": "p", "model": "m", "output_tokens": 2}],
    )
    write_explain(tmp_path, data)
    write_usage(tmp_path / "usage.jsonl", usage_row(run_id="other"))
    trace = GareRunSource().read(tmp_path)
    assert trace.unrecognised_by_type == {"other-run:other": 1}
    assert trace.coverage < 1.0


def test_undated_rows_keep_their_place_in_their_file(tmp_path: Path) -> None:
    data = explain(("created", {"goal": "fix it"}), ("route_decision", {}))
    write_explain(tmp_path, data)
    write_usage(
        tmp_path / "usage.jsonl",
        usage_row(task_id="undated-first", created_at=None),
        usage_row(task_id="dated", created_at="2026-10-08T12:00:05+00:00"),
        usage_row(task_id="undated-after", created_at="garbage"),
    )
    trace = GareRunSource().read(tmp_path)
    texts = [e.text for e in trace.events]
    # Nothing lands before the goal, and file order holds within the export.
    assert texts[0] == "fix it"
    usage = [t.split(":")[0] for t in texts if t.startswith(("undated", "dated"))]
    assert usage == ["undated-first", "dated", "undated-after"]
    assert texts.index("dated: p/m") > texts.index(": no route")


# -- a real recorded run (issue #55) -------------------------------------------


@pytest.mark.req("TER-SRC-011")
def test_a_real_mission_maps_its_attempts_review_and_outcome() -> None:
    trace = GareRunSource().read(REAL)
    assert trace.session_id == "c2ffdf8f1b09"
    assert kinds(REAL) == {
        EventKind.PROMPT: 1,
        EventKind.ROUTE_SELECTED: 4,
        EventKind.ATTEMPT_STARTED: 2,
        EventKind.RESPONSE: 4,
        EventKind.VERIFICATION_COMPLETED: 2,
        EventKind.OUTCOME_RECORDED: 1,
        EventKind.TASK_COMPLETED: 1,
    }
    assert trace.events[0].text.startswith("Fix the off-by-one bug in toy/stats.py")
    # Attempt 1, its diagnosis (a known state with no event), then repair attempt 2.
    attempts = [e.text for e in trace.events if e.kind is EventKind.ATTEMPT_STARTED]
    assert attempts == [
        "attempt 1 mission-coder-1-1c03ca90",
        "attempt 2 mission-coder-2-c3359cfc",
    ]
    calls = [e.text.split(":")[0] for e in trace.events if e.kind is EventKind.RESPONSE]
    assert calls == [
        "mission-coder-1-1c03ca90",
        "mission-investigate-1-89076b61",
        "mission-coder-2-c3359cfc",
        "mission-reviewer-review-2-24651b43",
    ]
    checks = [
        e.text for e in trace.events if e.kind is EventKind.VERIFICATION_COMPLETED
    ]
    assert checks == [
        "attempt 2 persona_review: fail",  # the reviewer said revise
        "attempt 2 attempt_complete: fail",  # test exit code 1
    ]
    assert [e.text for e in trace.events[-2:]] == [
        "needs_review: score 20/100",
        "run needs_review",
    ]
    assert trace.unrecognised == () and trace.coverage == 1.0
    # The unavailable route was skipped before any call: nothing failed over.
    assert EventKind.ROUTE_FAILOVER not in kinds(REAL)


@pytest.mark.req("TER-SRC-011")
def test_a_real_runs_usage_matches_both_exports() -> None:
    trace = GareRunSource().read(REAL)
    usage = [e.usage for e in trace.events if e.usage is not None]
    assert sum(u.input_tokens for u in usage) == 2814
    assert sum(u.output_tokens for u in usage) == 1526
    assert {u.model for u in usage} == {"qwen3-coder-next:latest"}
    # explain.json's route summary agrees with the usage export.
    routes = json.loads((REAL / "explain.json").read_text(encoding="utf-8"))["routes"]
    (summary,) = routes.values()
    assert (summary["input_tokens"], summary["output_tokens"]) == (2814, 1526)
    assert summary["calls"] == len(usage) == 4
    # GARE recorded no latency; a null latency does not stop the read.
    assert all(row["latency_ms"] is None for row in usage_rows(REAL))
    # explain.json alone gives the same totals from each task's final route.
    alone = GareRunSource().read(REAL / "explain.json")
    totals = [e.usage for e in alone.events if e.usage is not None]
    assert sum(u.input_tokens for u in totals) == 2814
    assert sum(u.output_tokens for u in totals) == 1526


@pytest.mark.req("TER-SRC-011")
@pytest.mark.parametrize("ref", [REAL, REAL / "explain.json"], ids=["both", "explain"])
def test_a_route_ranked_first_but_never_called_is_not_the_selected_route(
    ref: Path,
) -> None:
    trace = GareRunSource().read(ref)
    routes = [e.text for e in trace.events if e.kind is EventKind.ROUTE_SELECTED]
    skipped = " (rank 2 of 2; not called: ollama_down/qwen3-coder-next:notpulled)"
    assert routes == [
        "mission-coder-1-1c03ca90: ollama/qwen3-coder-next:latest" + skipped,
        "mission-investigate-1-89076b61: ollama/qwen3-coder-next:latest" + skipped,
        "mission-coder-2-c3359cfc: ollama/qwen3-coder-next:latest" + skipped,
        "mission-reviewer-review-2-24651b43: ollama/qwen3-coder-next:latest",
    ]


@pytest.mark.req("TER-SRC-011")
@pytest.mark.parametrize(
    "ref", [FAILOVER, FAILOVER / "explain.json"], ids=["both", "explain"]
)
def test_a_route_ranked_first_and_called_stays_selected(ref: Path) -> None:
    # The mock failover run called its top route (flaky), then failed over;
    # with explain.json alone the call is known from the recorded error.
    trace = GareRunSource().read(ref)
    routes = [e.text for e in trace.events if e.kind is EventKind.ROUTE_SELECTED]
    assert routes[0] == "research-a208947d: flaky/flaky-large (of 3 candidates)"
    assert not any("not called" in r for r in routes)


def test_a_route_decision_with_no_known_call_keeps_its_top_candidate(
    tmp_path: Path,
) -> None:
    candidates = [
        {"provider": "a", "model": "x", "score": 2.0},
        {"provider": "b", "model": "y", "score": 1.0},
    ]
    data = explain(("route_decision", {"task_id": "t", "candidates": candidates}))
    trace = GareRunSource().read(write_explain(tmp_path, data))
    assert [e.text for e in trace.events] == ["t: a/x (of 2 candidates)"]


def test_observe_and_explain_read_the_real_run() -> None:
    code, out, err = run_cli("observe", str(REAL), "--timeline")
    assert (code, err) == (0, "")
    assert "session c2ffdf8f1b09" in out
    assert "input 2,814 · output 1,526" in out
    code, out, _ = run_cli("explain", str(REAL))
    assert code == 0
    assert "no response text" in out and "no cache tokens" in out
