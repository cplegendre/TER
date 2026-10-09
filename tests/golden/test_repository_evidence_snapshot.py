"""Golden snapshots of repository evidence on the synthetic repository.

Freezes what the lexical baseline (``snapshots/repository/lexical.json``) and the
Python syntax-tree engine (``python-ast.json``) say about
``tests/contract/repository_fixture.REPO``: files, searches, test-to-source
links and, for the syntax tree, every file's structure. The lexical engine
is the deterministic baseline richer engines are measured against (P055), so
any change to its answers is a reviewed snapshot diff. Regenerate with
``TER_UPDATE_GOLDEN=1``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from tests.contract.repository_fixture import write_repo
from ter.adapters.driven.repository import (
    LexicalRepositoryEvidence,
    PythonSyntaxEvidence,
)
from ter.ports import RepositoryEvidence

from .conftest import assert_matches_snapshot

NEEDLES = ("add", "import", "pkg.core", "def ")


def _plain(value: object) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json.loads(json.dumps(dataclasses.asdict(value)))
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _evidence(engine: RepositoryEvidence) -> dict[str, Any]:
    files = engine.files()
    return {
        "engine": engine.name,
        "files": list(files),
        "search": {n: _plain(engine.search(n)) for n in NEEDLES},
        "tests_importing": {
            p: list(engine.tests_importing(p)) for p in files if p.endswith(".py")
        },
        "structure": {p: _plain(engine.structure(p)) for p in files},
    }


@pytest.mark.req("TER-EVD-002")
def test_lexical_evidence_matches_golden_snapshot(tmp_path: Path) -> None:
    engine = LexicalRepositoryEvidence(write_repo(tmp_path))
    assert_matches_snapshot("repository/lexical", _evidence(engine))


@pytest.mark.req("TER-EVD-012")
def test_python_syntax_evidence_matches_golden_snapshot(tmp_path: Path) -> None:
    engine = PythonSyntaxEvidence(write_repo(tmp_path))
    assert_matches_snapshot("repository/python-ast", _evidence(engine))
