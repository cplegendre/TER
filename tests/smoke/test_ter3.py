"""TER 3's ``ter`` command as installed (TER-TST-006, TER-TST-007).

Commands that score with the embedding model need the ``embeddings`` extra in
the environment that runs them. Where the extra is installed they must work;
where it is not, they must refuse with the install command, never crash.
"""

from __future__ import annotations

import json
import subprocess
from functools import cache
from pathlib import Path

import pytest

from .conftest import FIXTURE, HOOKS, REPO, SAMPLE, Cli, Result

BENCHMARK = REPO / "benchmarks" / "example_annotations.jsonl"
CANDIDATE = REPO / "benchmarks" / "example_annotations_candidate.jsonl"
#: How long ``ter watch`` must keep following a session to count as working.
WATCH_SECONDS = 20

pytestmark = pytest.mark.smoke


@cache
def _has_embeddings(python: str) -> bool:
    probe = subprocess.run(
        [python, "-c", "import sentence_transformers"], capture_output=True, check=False
    )
    return probe.returncode == 0


def _works_or_asks_for_embeddings(cli: Cli, name: str, result: Result) -> None:
    assert "Traceback" not in result.err, f"{name}: {result.err}"
    if _has_embeddings(cli.python):
        result.ok()
    else:
        assert result.code == 1, f"{name}: {result.out}"
        assert "sentence-transformers" in result.err, f"{name}: {result.err}"


@pytest.mark.req("TER-TST-006", "TER-TST-007")
def test_analysis_commands(cli: Cli, workdir: Path) -> None:
    runs = {
        "analyze": cli.ter("analyze", str(SAMPLE)),
        "report": cli.ter("report", str(SAMPLE)),
        "compare": cli.ter("compare", str(SAMPLE), str(FIXTURE), "--format", "json"),
        "visualize": cli.ter("visualize", str(SAMPLE)),
        "present": cli.ter("present", str(SAMPLE)),
    }
    for name, result in runs.items():
        _works_or_asks_for_embeddings(cli, name, result)


@pytest.mark.req("TER-TST-006")
def test_a3_and_explain(cli: Cli, workdir: Path) -> None:
    cli.ter("a3", str(SAMPLE), "--json", "ter3-a3.json").ok()
    a3 = json.loads((workdir / "ter3-a3.json").read_text(encoding="utf-8"))
    assert a3["schema"] == "ter.a3/0.1"
    assert "sample-session-001" in cli.ter("explain", str(SAMPLE)).ok().out


@pytest.mark.req("TER-TST-006")
def test_budget_json(cli: Cli) -> None:
    budget = json.loads(
        cli.ter("budget", "add a login page", "--format", "json").ok().out
    )
    assert isinstance(budget, dict) and budget


@pytest.mark.req("TER-TST-006")
def test_list_runs_without_sessions(cli: Cli, workdir: Path) -> None:
    empty = workdir / "no-sessions"
    empty.mkdir(exist_ok=True)
    assert json.loads(cli.ter("list", str(empty), "--format", "json").ok().out) == []


@pytest.mark.req("TER-TST-006")
@pytest.mark.parametrize("command", ["store", "graph", "delta", "check"])
def test_context_store_round_trip(cli: Cli, command: str) -> None:
    result = cli.ter("context", command, str(SAMPLE)).ok()
    assert "maximum recursion" not in result.err
    assert result.out.strip()


@pytest.mark.req("TER-TST-006")
def test_context_optimize_with_a_budget(cli: Cli) -> None:
    result = cli.ter("context", "optimize", str(SAMPLE), "--budget", "2000")
    _works_or_asks_for_embeddings(cli, "context optimize", result)


@pytest.mark.req("TER-TST-006")
def test_benchmark_scores_labelled_predictions(cli: Cli) -> None:
    report = json.loads(
        cli.ter(
            "benchmark", str(BENCHMARK), "--format", "json", "--bootstrap-samples", "50"
        )
        .ok()
        .out
    )
    assert report["binary"]["support"] == 6
    assert 0.0 <= report["binary"]["f1"] <= 1.0


@pytest.mark.req("TER-TST-006")
def test_benchmark_compare_reports_the_delta(cli: Cli) -> None:
    report = json.loads(
        cli.ter("benchmark-compare", str(BENCHMARK), str(CANDIDATE), "--format", "json")
        .ok()
        .out
    )
    assert report["baseline_records"] == report["candidate_records"] == 6
    accuracy = report["accuracy"]
    assert accuracy["delta"] == pytest.approx(
        accuracy["candidate"] - accuracy["baseline"]
    )


@pytest.mark.req("TER-TST-006")
def test_hook_monitor_answers_claude_code(cli: Cli) -> None:
    payload = (HOOKS / "post_tool_use_read.json").read_text(encoding="utf-8")
    result = cli.ter("hook", "monitor", stdin=payload).ok()
    assert isinstance(json.loads(result.out), dict)


@pytest.mark.req("TER-TST-007")
@pytest.mark.parametrize("stdin", ["", "{not json"], ids=["empty", "torn"])
def test_hook_monitor_never_blocks_on_a_bad_payload(cli: Cli, stdin: str) -> None:
    # Exit 2 would block the tool call in Claude Code; any other failure is
    # shown to the user and the agent carries on.
    result = cli.ter("hook", "monitor", stdin=stdin)
    assert result.code != 2, result.err
    assert "Traceback" not in result.err, result.err


@pytest.mark.req("TER-TST-006")
def test_watch_follows_a_session(cli: Cli, workdir: Path) -> None:
    argv = [cli.script("ter"), "watch", str(SAMPLE), "--stream", "--format", "json"]
    with subprocess.Popen(
        argv,
        cwd=workdir,
        env=cli.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as watch:
        try:
            out, err = watch.communicate(timeout=WATCH_SECONDS)
        except subprocess.TimeoutExpired:
            # Still following the session: watching works until stopped.
            watch.kill()
            out, err = watch.communicate()
            assert _has_embeddings(cli.python), err
            assert "Traceback" not in err, err
            return
    # It stopped by itself: only right without the embedding model.
    _works_or_asks_for_embeddings(cli, "watch", Result(watch.returncode, out, err))
    assert watch.returncode == 1, out


@pytest.mark.req("TER-TST-007")
@pytest.mark.parametrize("command", ["analyze", "report", "a3", "explain"])
def test_a_missing_session_is_refused(cli: Cli, command: str) -> None:
    result = cli.ter(command, "no-such-session.jsonl")
    assert result.code != 0, result.out
    assert result.err.strip() and "Traceback" not in result.err, result.err
