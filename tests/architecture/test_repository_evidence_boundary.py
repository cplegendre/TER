"""Repository evidence enters TER only through the RepositoryEvidence port.

TER-EVD-001 and P054: the only code that reads a repository's version
control or syntax trees is a repository engine
(``ter.adapters.driven.repository``), which implements the port; nothing
else in TER runs a subprocess or parses Python source, and the engines
import no model provider (the ``provider-neutral-evidence`` import
contract).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PYPROJECT = SRC.parent / "pyproject.toml"
ENGINES = SRC / "ter" / "adapters" / "driven" / "repository"

#: Modules that let code read a repository other than as plain files.
REPOSITORY_READERS = {"subprocess", "ast", "pygit2", "git", "dulwich", "tree_sitter"}

MODEL_PROVIDERS = {"anthropic", "tiktoken", "sentence_transformers", "gare", "httpx"}


def _imported(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.req("TER-EVD-001")
def test_only_repository_engines_read_version_control_or_syntax_trees() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(_imported(path) & REPOSITORY_READERS)
        for package in ("ter", "ter_calculator")
        for path in sorted((SRC / package).rglob("*.py"))
        if ENGINES not in path.parents and _imported(path) & REPOSITORY_READERS
    }
    assert offenders == {}


@pytest.mark.req("TER-EVD-001")
def test_the_evidence_layer_is_held_provider_neutral_by_an_import_contract() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    contracts = {c["id"]: c for c in config["tool"]["importlinter"]["contracts"]}
    contract = contracts["provider-neutral-evidence"]
    assert contract["type"] == "forbidden"
    assert set(contract["source_modules"]) == {
        "ter.domain.repository",
        "ter.adapters.driven.repository",
    }
    assert MODEL_PROVIDERS <= set(contract["forbidden_modules"])
    # The engines are one adapter: they never reach another adapter either.
    assert (
        "ter.adapters.driven.repository" in contracts["independent-adapters"]["modules"]
    )
