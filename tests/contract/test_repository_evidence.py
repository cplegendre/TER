"""Contract suite for the ``RepositoryEvidence`` port.

Every repository engine (lexical, Git, Python syntax tree, multi-language
syntax) and the in-memory fake are given the same synthetic repository and
must answer alike: the obligations in the port's docstring, one test each.
Evidence an engine does not have (syntax trees, diffs, history) is ``None``,
never a guess; what each richer engine returns is pinned by its unit tests.

A second synthetic repository, a TypeScript/Svelte monorepo
(``ecmascript_fixture``), holds every engine to one rule for languages
other than Python: an engine that reads a file's structure resolves its
imports to the files the fixture names and links its tests; an engine that
does not refuses the test question with ``UnsupportedLanguageError``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from ter.adapters.driven.in_memory import InMemoryRepositoryEvidence
from ter.adapters.driven.repository import (
    GitRepositoryEvidence,
    LexicalRepositoryEvidence,
    PythonSyntaxEvidence,
    SourceSyntaxEvidence,
)
from ter.domain.repository import (
    FileCommit,
    RepositoryDiff,
    RepositoryEvidenceError,
    SourceStructure,
    TextMatch,
    UnknownPathError,
    UnsupportedLanguageError,
    resolve_import,
)
from ter.ports import RepositoryEvidence

from .ecmascript_fixture import ES_LINKS, ES_REPO, ES_TESTS_OF
from .repository_fixture import IMPORTS, REPO, TESTS_OF, git_repo, write_repo

Factory = Callable[[Path], RepositoryEvidence]


def _lexical(tmp: Path) -> RepositoryEvidence:
    return LexicalRepositoryEvidence(write_repo(tmp))


def _python_ast(tmp: Path) -> RepositoryEvidence:
    return PythonSyntaxEvidence(write_repo(tmp))


def _git(tmp: Path) -> RepositoryEvidence:
    return GitRepositoryEvidence(git_repo(tmp / "repo"))


def _syntax(tmp: Path) -> RepositoryEvidence:
    return SourceSyntaxEvidence(write_repo(tmp))


def _memory(tmp: Path) -> RepositoryEvidence:
    return InMemoryRepositoryEvidence(REPO, imports=IMPORTS)


ENGINES = [_lexical, _git, _python_ast, _syntax, _memory]
ENGINE_IDS = ["lexical", "git", "python-ast", "syntax", "in-memory"]


@pytest.fixture(params=ENGINES, ids=ENGINE_IDS)
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> RepositoryEvidence:
    factory: Factory = request.param
    return factory(tmp_path)


ES_ENGINES: dict[str, Factory] = {
    "lexical": lambda tmp: LexicalRepositoryEvidence(write_repo(tmp, ES_REPO)),
    "git": lambda tmp: GitRepositoryEvidence(git_repo(tmp / "repo", ES_REPO)),
    "python-ast": lambda tmp: PythonSyntaxEvidence(write_repo(tmp, ES_REPO)),
    "syntax": lambda tmp: SourceSyntaxEvidence(write_repo(tmp, ES_REPO)),
    "in-memory": lambda tmp: InMemoryRepositoryEvidence(ES_REPO),
}


@pytest.fixture(params=sorted(ES_ENGINES))
def es_engine(request: pytest.FixtureRequest, tmp_path: Path) -> RepositoryEvidence:
    return ES_ENGINES[request.param](tmp_path)


def _reads_ecmascript(engine: RepositoryEvidence) -> bool:
    return engine.structure("apps/web/src/lib/api.ts") is not None


def _expected_matches(needle: str) -> list[tuple[str, int, str]]:
    return [
        (path, number, line)
        for path in sorted(REPO)
        for number, line in enumerate(REPO[path].splitlines(), start=1)
        if re.search(needle, line)
    ]


@pytest.mark.req("TER-EVD-001")
def test_satisfies_the_port_protocol(engine: RepositoryEvidence) -> None:
    assert isinstance(engine, RepositoryEvidence)
    assert engine.name


@pytest.mark.req("TER-EVD-001")
def test_lists_every_file_relative_sorted_and_without_version_control(
    engine: RepositoryEvidence,
) -> None:
    files = engine.files()
    assert files == tuple(sorted(REPO))
    assert not any(p.startswith(("/", ".git/")) or "\\" in p for p in files)


@pytest.mark.req("TER-EVD-001")
def test_text_returns_a_listed_file(engine: RepositoryEvidence) -> None:
    for path, text in REPO.items():
        assert engine.text(path) == text


@pytest.mark.req("TER-EVD-001")
@pytest.mark.parametrize(
    "ask",
    [
        lambda e, p: e.text(p),
        lambda e, p: e.tests_importing(p),
        lambda e, p: e.structure(p),
        lambda e, p: e.history(p),
    ],
    ids=["text", "tests_importing", "structure", "history"],
)
def test_an_unknown_path_raises_unknown_path_error(
    engine: RepositoryEvidence,
    ask: Callable[[RepositoryEvidence, str], object],
) -> None:
    for path in ("src/pkg/missing.py", "../outside.py", "/etc/passwd"):
        with pytest.raises(UnknownPathError, match=re.escape(path)):
            ask(engine, path)


@pytest.mark.req("TER-EVD-001")
@pytest.mark.parametrize("needle", ["add(", "pkg.core", "import", "no such text"])
def test_search_finds_every_line_containing_the_text(
    engine: RepositoryEvidence, needle: str
) -> None:
    found = engine.search(needle)
    assert all(isinstance(m, TextMatch) for m in found)
    assert [(m.path, m.line, m.text) for m in found] == _expected_matches(
        re.escape(needle)
    )


@pytest.mark.req("TER-EVD-001")
def test_search_takes_a_regular_expression_when_asked(
    engine: RepositoryEvidence,
) -> None:
    pattern = r"^def \w+\("
    found = engine.search(pattern, regex=True)
    assert [(m.path, m.line, m.text) for m in found] == _expected_matches(pattern)
    assert engine.search("def add(", regex=False)  # literal: the paren is text


@pytest.mark.req("TER-EVD-001")
def test_an_empty_search_is_refused(engine: RepositoryEvidence) -> None:
    with pytest.raises(RepositoryEvidenceError):
        engine.search("")


@pytest.mark.req("TER-EVD-003")
@pytest.mark.parametrize("source", sorted(TESTS_OF))
def test_returns_every_test_module_that_imports_a_source_module(
    engine: RepositoryEvidence, source: str
) -> None:
    assert engine.tests_importing(source) == TESTS_OF[source]


@pytest.mark.req("TER-EVD-003")
def test_tests_of_a_non_python_file_are_unsupported(
    engine: RepositoryEvidence,
) -> None:
    with pytest.raises(UnsupportedLanguageError):
        engine.tests_importing("README.md")


@pytest.mark.req("TER-EVD-001")
def test_every_answer_is_the_same_on_every_call(engine: RepositoryEvidence) -> None:
    def everything() -> object:
        return (
            engine.files(),
            engine.search("add"),
            {p: engine.tests_importing(p) for p in TESTS_OF},
            {p: engine.structure(p) for p in REPO},
            engine.diff(),
            {p: engine.history(p) for p in REPO},
        )

    assert everything() == everything()


@pytest.mark.req("TER-EVD-012")
def test_structure_is_a_syntax_tree_or_none(engine: RepositoryEvidence) -> None:
    for path in REPO:
        structure = engine.structure(path)
        assert structure is None or (
            isinstance(structure, SourceStructure) and structure.path == path
        )
    assert engine.structure("README.md") is None


@pytest.mark.req("TER-EVD-012")
@pytest.mark.req("TER-EVD-007")
def test_structure_of_a_files_own_text_is_its_structure(
    engine: RepositoryEvidence,
) -> None:
    for path in REPO:
        assert engine.structure_of(path, engine.text(path)) == engine.structure(path)


@pytest.mark.req("TER-EVD-012")
@pytest.mark.req("TER-EVD-007")
def test_structure_of_serves_a_path_the_repository_does_not_list(
    engine: RepositoryEvidence,
) -> None:
    # A file a session creates: answered without UnknownPathError, and a
    # syntax tree exactly when the engine has them for the language.
    text = "from pkg.core import add\n"
    has_trees = engine.structure("src/pkg/core.py") is not None
    created = engine.structure_of("src/pkg/new.py", text)
    assert (created is not None) is has_trees
    if created is not None:
        assert created.path == "src/pkg/new.py" and created.module == "pkg.new"
        assert [e.module for e in created.imports] == ["pkg.core"]
    assert engine.structure_of("docs/new.md", "# x\n") is None


@pytest.mark.req("TER-EVD-013")
def test_diff_and_history_are_version_control_evidence_or_none(
    engine: RepositoryEvidence,
) -> None:
    diff = engine.diff()
    assert diff is None or isinstance(diff, RepositoryDiff)
    for path in REPO:
        history = engine.history(path)
        assert history is None or all(isinstance(c, FileCommit) for c in history)


# -- languages other than Python: a TypeScript/Svelte monorepo ----------------


@pytest.mark.req("TER-EVD-012")
def test_an_engine_that_reads_a_language_resolves_its_imports_to_files(
    es_engine: RepositoryEvidence,
) -> None:
    if not _reads_ecmascript(es_engine):
        assert all(es_engine.structure(p) is None for p in ES_LINKS)
        return
    files = frozenset(es_engine.files())
    for path, links in ES_LINKS.items():
        structure = es_engine.structure(path)
        assert structure is not None and structure.error is None, path
        assert structure.path == path and structure.module == path
        assert tuple(resolve_import(e, files) for e in structure.imports) == links


@pytest.mark.req("TER-EVD-014")
@pytest.mark.parametrize("source", sorted(ES_TESTS_OF))
def test_tests_of_another_language_are_linked_or_refused(
    es_engine: RepositoryEvidence, source: str
) -> None:
    if _reads_ecmascript(es_engine):
        assert es_engine.tests_importing(source) == ES_TESTS_OF[source]
    else:
        with pytest.raises(UnsupportedLanguageError):
            es_engine.tests_importing(source)


@pytest.mark.req("TER-EVD-014")
def test_installed_packages_are_neither_sources_nor_tests(
    es_engine: RepositoryEvidence,
) -> None:
    vendored = "apps/web/node_modules/dep/index.js"
    if _reads_ecmascript(es_engine):
        assert es_engine.tests_importing(vendored) == ()
        assert es_engine.tests_importing("apps/web/src/lib/theme.ts") == ()
    else:
        with pytest.raises(UnsupportedLanguageError):
            es_engine.tests_importing(vendored)


@pytest.mark.req("TER-EVD-012")
@pytest.mark.req("TER-EVD-007")
def test_structure_of_a_files_own_text_is_its_structure_in_every_language(
    es_engine: RepositoryEvidence,
) -> None:
    for path in ES_REPO:
        text = es_engine.text(path)
        assert es_engine.structure_of(path, text) == es_engine.structure(path)


@pytest.mark.req("TER-EVD-012")
@pytest.mark.req("TER-EVD-007")
def test_structure_of_a_created_file_names_the_files_it_may_load(
    es_engine: RepositoryEvidence,
) -> None:
    text = "import { formatTitle } from '@shared/util';\nimport { x } from './later';\n"
    created = es_engine.structure_of("apps/web/src/lib/new.ts", text)
    if not _reads_ecmascript(es_engine):
        assert created is None
        return
    assert created is not None and created.module == "apps/web/src/lib/new.ts"
    util, later = created.imports
    assert resolve_import(util, es_engine.files()) == "packages/shared/src/util.ts"
    # A file the session creates later still resolves: its path is a candidate.
    assert "apps/web/src/lib/later.ts" in later.candidates
    assert resolve_import(later, es_engine.files()) is None
