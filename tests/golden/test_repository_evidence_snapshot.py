"""Golden snapshots of repository evidence on the synthetic repository.

Freezes what the lexical baseline (``snapshots/repository/lexical.json``) and the
Python syntax-tree engine (``python-ast.json``) say about
``tests/contract/repository_fixture.REPO``: files, searches, test-to-source
links and, for the syntax tree, every file's structure. The lexical engine
is the deterministic baseline richer engines are measured against (P055), so
any change to its answers is a reviewed snapshot diff. ``syntax.json``
freezes what the multi-language ``syntax`` engine says about the
TypeScript/Svelte monorepo of ``tests/contract/ecmascript_fixture``: every
source file's structure (imports with their candidate files, definitions),
the file each import loads and every source file's tests. Regenerate with
``TER_UPDATE_GOLDEN=1``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from tests.contract.ecmascript_fixture import ES_REPO
from tests.contract.repository_fixture import write_repo
from ter.adapters.driven.repository import (
    LexicalRepositoryEvidence,
    PythonSyntaxEvidence,
    SourceSyntaxEvidence,
)
from ter.domain.repository import resolve_import, source_language
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


@pytest.mark.req("TER-EVD-012")
@pytest.mark.req("TER-EVD-014")
def test_multi_language_syntax_evidence_matches_golden_snapshot(
    tmp_path: Path,
) -> None:
    engine = SourceSyntaxEvidence(write_repo(tmp_path, ES_REPO))
    files = engine.files()
    sources = [p for p in files if source_language(p) is not None]
    structures = {p: engine.structure(p) for p in sources}
    assert_matches_snapshot(
        "repository/syntax",
        {
            "engine": engine.name,
            "files": list(files),
            "structure": {p: _plain(s) for p, s in structures.items()},
            "loads": {
                p: [resolve_import(e, files) for e in s.imports]
                for p, s in structures.items()
                if s is not None
            },
            "tests_importing": {p: list(engine.tests_importing(p)) for p in sources},
        },
    )
