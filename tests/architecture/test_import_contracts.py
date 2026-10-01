"""Architecture fitness tests: the hexagon's dependency rules hold.

The rules themselves live in ``[tool.importlinter]`` in pyproject.toml so the
``lint-imports`` CI step and this test enforce exactly the same contracts.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

EXPECTED_CONTRACTS = {
    "hexagon-layers",
    "pure-domain",
    "vendor-free-core",
    "independent-adapters",
    "ter3-uses-hexagon-edges",
    "report-renderers",
    "behaviour-blind-to-outcome",
}


@pytest.mark.req("TER-ARC-001")
def test_every_import_contract_is_kept() -> None:
    # A subprocess keeps import-linter's logging configuration (which disables
    # existing loggers) from leaking into the rest of the test session.
    script = (
        "import sys; from importlinter.cli import lint_imports; "
        f"sys.exit(lint_imports(config_filename={str(PYPROJECT)!r}))"
    )
    run = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.req("TER-ARC-001")
def test_hexagon_contracts_are_declared() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = {c["id"] for c in config["tool"]["importlinter"]["contracts"]}
    assert EXPECTED_CONTRACTS <= declared
    layers = next(
        c["layers"]
        for c in config["tool"]["importlinter"]["contracts"]
        if c["id"] == "hexagon-layers"
    )
    assert layers[-1] == "ter.domain", "the domain must be the innermost layer"


EXTERNAL_CAPABILITY_PACKAGES = {"gare", "pydantic", "httpx"}


@pytest.mark.req("TER-ARC-003")
def test_core_never_imports_external_capability_packages() -> None:
    # ADR 0005: an adapter reads an outside system's files by schema name;
    # the domain, ports and use cases never import its package or stack.
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    contracts = {c["id"]: c for c in config["tool"]["importlinter"]["contracts"]}
    for contract_id in ("pure-domain", "vendor-free-core"):
        forbidden = set(contracts[contract_id]["forbidden_modules"])
        missing = EXTERNAL_CAPABILITY_PACKAGES - forbidden
        assert not missing, f"{contract_id} does not forbid {sorted(missing)}"
    assert {"ter.ports", "ter.application"} <= set(
        contracts["vendor-free-core"]["source_modules"]
    )
    assert contracts["pure-domain"]["source_modules"] == ["ter.domain"]
