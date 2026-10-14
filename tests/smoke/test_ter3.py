"""TER 3's ``ter`` command as installed (TER-TST-006, TER-TST-007).

Commands that score with the embedding model need the ``embeddings`` extra in
the environment that runs them. Where the extra is installed they must work;
where it is not, they must refuse with the install command, never crash.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from .conftest import FIXTURE, SAMPLE, Cli, Result

pytestmark = pytest.mark.smoke


@cache
def _has_embeddings(python: str) -> bool:
    import subprocess

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
@pytest.mark.parametrize("command", ["benchmark", "benchmark-compare", "hook", "watch"])
def test_help_of_commands_that_need_labelled_data(cli: Cli, command: str) -> None:
    # These read labelled predictions, a live hook stream or a terminal; their
    # behaviour is covered in tests/unit, the smoke test proves they load.
    assert "usage:" in cli.ter(command, "--help").ok().out


@pytest.mark.req("TER-TST-007")
@pytest.mark.parametrize("command", ["analyze", "report", "a3", "explain"])
def test_a_missing_session_is_refused(cli: Cli, command: str) -> None:
    result = cli.ter(command, "no-such-session.jsonl")
    assert result.code != 0, result.out
    assert result.err.strip() and "Traceback" not in result.err, result.err
