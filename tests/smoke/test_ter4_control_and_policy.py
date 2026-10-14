"""Control charts and L4 policies end to end: measure, limits, chart, then
check, approve, shadow and effectiveness, with every file TER writes checked
against its JSON Schema (TER-TST-006, TER-TST-007)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from schema.conftest import assert_conforms

from .conftest import FIXTURE, GOLDEN_SESSIONS, POLICIES, SAMPLE, Cli

pytestmark = pytest.mark.smoke


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def limits(cli: Cli, workdir: Path) -> Path:
    cli.ter4(
        "control",
        "measure",
        str(GOLDEN_SESSIONS),
        str(SAMPLE),
        str(FIXTURE),
        "--out",
        "measures.json",
    ).ok()
    assert_conforms("ter.control-measures/1", _read(workdir / "measures.json"))
    cli.ter4(
        "control",
        "limits",
        "measures.json",
        "--out",
        "limits.json",
        "--date",
        "2026-10-14",
    ).ok()
    assert_conforms("ter.control-limits/1", _read(workdir / "limits.json"))
    return workdir / "limits.json"


@pytest.mark.req("TER-TST-006")
def test_control_charts_from_measures(cli: Cli, workdir: Path, limits: Path) -> None:
    cli.ter4(
        "control",
        "chart",
        "measures.json",
        "--limits",
        str(limits),
        "--html",
        "control.html",
        "--json",
        "control.json",
    ).ok()
    assert "</html>" in (workdir / "control.html").read_text(encoding="utf-8")
    assert _read(workdir / "control.json")["schema"] == "ter.control-report/1"
    cli.ter4("a3", str(SAMPLE), "--limits", str(limits), "--html", "placed.html").ok()


@pytest.mark.req("TER-TST-006")
def test_policy_approve_shadow_effectiveness(
    cli: Cli, workdir: Path, limits: Path
) -> None:
    policies = workdir / "policies.json"
    shutil.copy(POLICIES, policies)
    assert cli.ter4("policy", "check", str(policies)).code == 1  # unapproved
    cli.ter4(
        "policy", "approve", str(policies), "--by", "Smoke", "--date", "2026-10-14"
    ).ok()
    cli.ter4("policy", "check", str(policies)).ok()
    assert_conforms("ter.intervention-policies/1", _read(policies))

    cli.ter4(
        "policy",
        "shadow",
        str(policies),
        str(GOLDEN_SESSIONS),
        "--ledger",
        "ledger",
        "--judge",
        "judge.csv",
        "--limits",
        str(limits),
    ).ok()
    lines = [
        json.loads(line)
        for path in sorted((workdir / "ledger").iterdir())
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert lines
    for line in lines:
        assert_conforms("ter.intervention-ledger/1", line)
    assert (workdir / "judge.csv").read_text(encoding="utf-8").strip()

    cli.ter4("policy", "effectiveness", "--ledger", "ledger", "--json", "eff.json").ok()
    assert _read(workdir / "eff.json")["schema"] == "ter.policy-effectiveness/1"


@pytest.mark.req("TER-TST-007")
def test_an_edited_policy_file_is_refused_until_approved_again(
    cli: Cli, workdir: Path
) -> None:
    policies = workdir / "edited.json"
    shutil.copy(POLICIES, policies)
    cli.ter4("policy", "approve", str(policies), "--by", "Smoke").ok()
    doc = _read(policies)
    doc["policies"][0]["min_evidence"] += 1
    policies.write_text(json.dumps(doc), encoding="utf-8")
    result = cli.ter4("policy", "shadow", str(policies), str(SAMPLE), "--ledger", "l2")
    assert result.code != 0 and "Traceback" not in result.err
    assert not (workdir / "l2").exists() or not any((workdir / "l2").iterdir())


@pytest.mark.req("TER-TST-007")
@pytest.mark.parametrize(
    "argv",
    [
        ["policy", "check", "broken.json"],
        ["control", "limits", "broken.json", "--out", "x.json"],
        ["control", "chart", "broken.json", "--limits", "broken.json"],
        ["policy", "effectiveness", "--ledger", "broken.json"],
    ],
    ids=["policy", "limits", "chart", "effectiveness"],
)
def test_malformed_files_are_refused(cli: Cli, workdir: Path, argv: list[str]) -> None:
    (workdir / "broken.json").write_text("{not json", encoding="utf-8")
    result = cli.ter4(*argv)
    assert result.code != 0, result.out
    assert result.err.strip() and "Traceback" not in result.err, result.err
