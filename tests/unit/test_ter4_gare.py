"""The GARE session source: exported GARE runs as ter.event traces (issue #52)."""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.event_log.codec import event_from_record, event_to_record
from ter.adapters.driven.gare import GARE_USAGE_SCHEMA, NO_CACHE_TOKENS, GareRunSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import format_report, main
from ter.application.observe import AnalyseTrace
from ter.bootstrap import cli_services, session_source_for
from ter.domain import Actor, EventKind
from ter.domain.events import EVENT_SCHEMA_VERSION, READABLE_SCHEMA_VERSIONS
from ter.domain.stream import EventClass
from tests.golden.corpus import CORPUS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gare"
FAILOVER = FIXTURES / "failover-run"
MISSION = FIXTURES / "repair-mission"
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
    assert EVENT_SCHEMA_VERSION == "ter.event/0.3"
    assert {"ter.event/0.1", "ter.event/0.2"} < READABLE_SCHEMA_VERSIONS
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
@pytest.mark.parametrize("run", [FAILOVER, MISSION], ids=["failover", "mission"])
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
@pytest.mark.parametrize("run", [FAILOVER, MISSION], ids=["failover", "mission"])
def test_gare_traces_carry_no_cache_tokens_and_say_so(run: Path) -> None:
    trace = GareRunSource().read(run)
    assert trace.usage_limits == (NO_CACHE_TOKENS,)
    for event in trace.events:
        if event.usage is not None:
            assert event.usage.cache_creation_tokens == 0
            assert event.usage.cache_read_tokens == 0


@pytest.mark.req("TER-SRC-014")
def test_reports_state_the_usage_limit_beside_their_token_figures() -> None:
    report = AnalyseTrace(GareRunSource(), RegexTokenizer())(FAILOVER)
    assert report.usage_limits == (NO_CACHE_TOKENS,)
    assert report.as_dict()["usage_limits"] == [NO_CACHE_TOKENS]
    usage_line = next(
        line for line in format_report(report).splitlines() if "usage" in line
    )
    assert "reports no cache tokens" in usage_line


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
