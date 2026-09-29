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
