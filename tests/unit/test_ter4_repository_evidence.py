"""Repository evidence engines on small synthetic repositories (L3, step 1-3).

Lexical determinism (TER-EVD-002), test-to-source links (TER-EVD-003),
Python syntax trees (TER-EVD-012), Git diff and history (TER-EVD-013), and
repository engines loading through the capability registry (TER-ARC-007).
Each repository is built in ``tmp_path``; Git repositories use a fixed
identity and fixed dates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.contract.repository_fixture import REPO, TESTS_OF, git, git_repo, write_repo
from ter.adapters.driven.repository import (
    GitRepositoryEvidence,
    LexicalRepositoryEvidence,
    PythonSyntaxEvidence,
)
from ter.adapters.driven.repository.lexical import lexical_imports
from ter.bootstrap.capabilities import (
    BUILTIN_CAPABILITIES,
    CapabilityRegistry,
    default_registry,
    repository_evidence,
)
from ter.domain.capabilities import CapabilityError, UnknownCapabilityError
from ter.domain.repository import (
    MODULE_LEVEL,
    CallEdge,
    ChangeStatus,
    ImportEdge,
    NotAWorkTreeError,
    RepositoryEvidenceError,
    Symbol,
    SymbolKind,
    UnknownPathError,
    UnsupportedLanguageError,
    import_candidates,
    imports_module,
    is_test_module,
    module_name,
    resolve_relative,
)
from ter.ports import RepositoryEvidence


def _answers(engine: RepositoryEvidence) -> dict[str, Any]:
    """Everything an engine says about its repository."""
    files = engine.files()
    return {
        "files": files,
        "search": [engine.search(n) for n in ("add", "import", "pkg.core")],
        "regex": engine.search(r"def \w+", regex=True),
        "tests": {p: engine.tests_importing(p) for p in files if p.endswith(".py")},
        "structure": {p: engine.structure(p) for p in files},
        "diff": engine.diff(),
        "history": {p: engine.history(p) for p in files},
    }


# -- shared rules (domain) ----------------------------------------------------


class TestSharedRules:
    def test_test_modules_follow_pytest_naming(self) -> None:
        assert is_test_module("tests/test_core.py")
        assert is_test_module("a/b/core_test.py")
        assert not is_test_module("tests/helpers.py")
        assert not is_test_module("tests/test_data.json")
        assert not is_test_module("testing.py")

    def test_module_names_follow_packages_up_to_an_import_root(self) -> None:
        files = set(REPO)
        assert module_name("src/pkg/core.py", files) == "pkg.core"
        assert module_name("src/pkg/__init__.py", files) == "pkg"
        assert module_name("tests/test_core.py", files) == "test_core"
        with pytest.raises(UnsupportedLanguageError):
            module_name("README.md", files)

    def test_relative_imports_resolve_against_the_package(self) -> None:
        assert resolve_relative(1, "core", "pkg") == "pkg.core"
        assert resolve_relative(1, None, "pkg") == "pkg"
        assert resolve_relative(2, "core", "pkg.tests") == "pkg.core"
        assert resolve_relative(0, "json", "pkg") == "json"
        # Climbing above the top-level package cannot be resolved.
        assert resolve_relative(2, "core", "pkg") == "..core"
        assert resolve_relative(1, "x", "") == ".x"

    @pytest.mark.req("TER-EVD-003")
    def test_importing_a_name_loads_its_module_and_every_parent(self) -> None:
        assert imports_module("pkg.core", "pkg.core")
        assert imports_module("pkg.core.add", "pkg.core")
        assert imports_module("pkg.util", "pkg")
        # Boundary: a module whose name merely starts the same is another module.
        assert not imports_module("pkg.core_extra", "pkg.core")
        assert not imports_module("pkg", "pkg.core")
        assert import_candidates(ImportEdge("pkg", ("core", "*"), 1)) == (
            "pkg",
            "pkg.core",
        )
        assert import_candidates(ImportEdge("..up", ("x",), 1)) == ()


# -- lexical engine ------------------------------------------------------------


class TestLexicalDeterminism:
    @pytest.mark.req("TER-EVD-002")
    def test_identical_content_gives_identical_results_wherever_it_lives(
        self, tmp_path: Path
    ) -> None:
        first = write_repo(tmp_path / "first")
        # Same bytes, written in the reverse order, at another path, with
        # other modification times.
        second = tmp_path / "elsewhere" / "second"
        write_repo(second, dict(reversed(list(REPO.items()))))
        for i, path in enumerate(sorted(REPO)):
            os.utime(second / path, (1_000_000 + i, 1_000_000 + i))
        a = LexicalRepositoryEvidence(first)
        b = LexicalRepositoryEvidence(second)
        assert _answers(a) == _answers(b)
        assert _answers(a) == _answers(a)

    @pytest.mark.req("TER-EVD-002")
    def test_results_follow_content_not_location(self, tmp_path: Path) -> None:
        # Negative: different content gives different results.
        first = LexicalRepositoryEvidence(write_repo(tmp_path / "a"))
        second = LexicalRepositoryEvidence(
            write_repo(tmp_path / "b", {**REPO, "tests/test_core.py": "import json\n"})
        )
        assert _answers(first) != _answers(second)
        assert second.tests_importing("src/pkg/core.py") == (
            "tests/test_both.py",
            "tests/unit/core_test.py",
        )

    @pytest.mark.req("TER-EVD-002")
    def test_version_control_directories_and_symlinks_are_not_content(
        self, tmp_path: Path
    ) -> None:
        root = write_repo(tmp_path / "r")
        write_repo(root, {".git/config": "x", ".hg/store": "y", ".svn/z": "add"})
        (root / "link.py").symlink_to(root / "src/pkg/core.py")
        engine = LexicalRepositoryEvidence(root)
        assert engine.files() == tuple(sorted(REPO))
        assert all(not m.path.startswith(".") for m in engine.search("add"))

    @pytest.mark.req("TER-EVD-002")
    def test_membership_without_a_walk_agrees_with_the_listing(
        self, tmp_path: Path
    ) -> None:
        root = write_repo(tmp_path / "r")
        write_repo(root, {".git/config": "x", "src/.git/x.py": "y"})
        (root / "link.py").symlink_to(root / "src/pkg/core.py")
        (root / "linked").symlink_to(root / "src")
        engine = LexicalRepositoryEvidence(root)
        listing = engine.listing()
        probes = [
            *REPO,
            ".git/config",
            "src/.git/x.py",
            "link.py",
            "linked/pkg/core.py",
            "src",
            "/src/pkg/core.py",
            "src//pkg/core.py",
            "src/pkg/../pkg/core.py",
            "missing.py",
        ]
        for path in probes:
            assert (path in listing) is (path in engine.files()), path
        assert sorted(listing) == list(engine.files()) and len(listing) == len(REPO)
        assert "new.py" in engine.listing(extra="new.py")
        with pytest.raises(UnknownPathError):
            engine.text("linked/pkg/core.py")

    def test_binary_files_are_listed_but_never_searched(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path / "r")
        (root / "blob.bin").write_bytes(b"add\0\xff")
        (root / "latin.txt").write_bytes("add caf\xe9".encode("latin-1"))
        engine = LexicalRepositoryEvidence(root)
        assert {"blob.bin", "latin.txt"} <= set(engine.files())
        assert {m.path for m in engine.search("add")}.isdisjoint(
            {"blob.bin", "latin.txt"}
        )
        with pytest.raises(RepositoryEvidenceError, match="UTF-8"):
            engine.text("blob.bin")

    def test_lines_are_numbered_as_editors_number_them(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path / "r", {"a.txt": "one\r\ntwo add\r\n\r\nadd\n"})
        found = LexicalRepositoryEvidence(root).search("add")
        assert [(m.line, m.text) for m in found] == [(2, "two add"), (4, "add")]

    def test_a_missing_root_or_bad_pattern_fails_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(RepositoryEvidenceError, match="not a directory"):
            LexicalRepositoryEvidence(tmp_path / "missing")
        engine = LexicalRepositoryEvidence(write_repo(tmp_path / "r"))
        with pytest.raises(RepositoryEvidenceError, match="invalid pattern"):
            engine.search("(", regex=True)

    @pytest.mark.req("TER-EVD-012")
    def test_the_lexical_engine_has_no_syntax_tree_or_history(
        self, tmp_path: Path
    ) -> None:
        engine = LexicalRepositoryEvidence(write_repo(tmp_path / "r"))
        assert engine.structure("src/pkg/core.py") is None
        assert engine.diff() is None
        assert engine.history("src/pkg/core.py") is None


class TestLexicalImports:
    def test_reads_every_import_statement_form(self) -> None:
        text = (
            "import json, pkg.core as c\n"
            "from pkg import \\\n"
            "    util\n"
            "from pkg.core import (\n"
            "    add,  # comment\n"
            "    Calc as K,\n"
            ")\n"
            "    from . import sibling\n"
            "x = 1; import os\n"
        )
        assert lexical_imports(text, "pkg.tests") == (
            ImportEdge("json", (), 1),
            ImportEdge("pkg.core", (), 1),
            ImportEdge("pkg", ("util",), 2),
            ImportEdge("pkg.core", ("add", "Calc"), 4),
            ImportEdge("pkg.tests", ("sibling",), 8),
        )


class TestTestsImporting:
    def _engines(self, root: Path) -> list[RepositoryEvidence]:
        return [LexicalRepositoryEvidence(root), PythonSyntaxEvidence(root)]

    @pytest.mark.req("TER-EVD-003")
    def test_relative_imports_inside_a_test_package_count(self, tmp_path: Path) -> None:
        root = write_repo(
            tmp_path / "r",
            {
                **REPO,
                "src/pkg/tests/__init__.py": "",
                "src/pkg/tests/test_rel.py": "from ..core import add\n",
                "src/pkg/tests/test_up.py": "from .. import util\n",
            },
        )
        for engine in self._engines(root):
            assert "src/pkg/tests/test_rel.py" in engine.tests_importing(
                "src/pkg/core.py"
            )
            assert "src/pkg/tests/test_up.py" in engine.tests_importing(
                "src/pkg/util.py"
            )
            assert "src/pkg/tests/test_up.py" not in engine.tests_importing(
                "src/pkg/core.py"
            )

    @pytest.mark.req("TER-EVD-003")
    def test_only_test_modules_and_only_that_module_are_returned(
        self, tmp_path: Path
    ) -> None:
        root = write_repo(
            tmp_path / "r",
            {
                **REPO,
                "src/pkg/core_extra.py": "",
                "tests/test_extra.py": "import pkg.core_extra\n",
                "tests/conftest.py": "from pkg.core import add\n",
            },
        )
        for engine in self._engines(root):
            # Boundary: core_extra is not core; conftest and helpers are not tests.
            assert (
                engine.tests_importing("src/pkg/core.py") == TESTS_OF["src/pkg/core.py"]
            )
            assert engine.tests_importing("src/pkg/core_extra.py") == (
                "tests/test_extra.py",
            )

    @pytest.mark.req("TER-EVD-003")
    def test_a_module_no_test_imports_has_none(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path / "r", {**REPO, "src/pkg/lonely.py": "X = 1\n"})
        for engine in self._engines(root):
            assert engine.tests_importing("src/pkg/lonely.py") == ()

    @pytest.mark.req("TER-EVD-003")
    def test_the_syntax_tree_engine_ignores_imports_written_in_strings(
        self, tmp_path: Path
    ) -> None:
        root = write_repo(
            tmp_path / "r",
            {
                **REPO,
                "tests/test_doc.py": '"""\nimport pkg.util\n"""\n',
                "tests/test_broken.py": "import pkg.util\ndef (:\n",
            },
        )
        lexical = LexicalRepositoryEvidence(root).tests_importing("src/pkg/util.py")
        syntax = PythonSyntaxEvidence(root).tests_importing("src/pkg/util.py")
        assert "tests/test_doc.py" in lexical  # the documented lexical limit
        assert "tests/test_doc.py" not in syntax
        # A test file that does not parse falls back to the lexical reading.
        assert "tests/test_broken.py" in syntax


# -- Python syntax-tree engine --------------------------------------------------


SOURCE = """\
import os
import numpy as np
from .core import add as plus
from . import util


def register(fn):
    return fn


@register
class Service(Base):
    def run(self):
        self.helper()
        return plus(1, 2)

    async def fetch(self):
        def inner():
            return os.path.join("a", "b")

        return inner()


print(np.array([1]))
values = [x() for x in []]
"""


class TestPythonSyntax:
    def _structure(self, tmp_path: Path, files: dict[str, str]) -> Any:
        root = write_repo(tmp_path / "r", {**REPO, **files})
        engine = PythonSyntaxEvidence(root)
        return engine

    @pytest.mark.req("TER-EVD-012")
    def test_symbols_imports_and_call_edges_of_the_fixture(
        self, tmp_path: Path
    ) -> None:
        engine = self._structure(tmp_path, {})
        core = engine.structure("src/pkg/core.py")
        assert core is not None and core.error is None
        assert (core.language, core.module) == ("python", "pkg.core")
        assert core.symbols == (
            Symbol("add", SymbolKind.FUNCTION, 1, 2),
            Symbol("Calc", SymbolKind.CLASS, 5, 7),
            Symbol("Calc.total", SymbolKind.METHOD, 6, 7),
        )
        assert core.imports == ()
        assert core.calls == (
            CallEdge("Calc.total", "sum", 7, None),
            CallEdge("Calc.total", "add", 7, "pkg.core.add"),
        )
        util = engine.structure("src/pkg/util.py")
        assert util is not None
        assert util.imports == (ImportEdge("pkg.core", ("add",), 1),)
        assert util.calls == (CallEdge("twice", "add", 5, "pkg.core.add"),)

    @pytest.mark.req("TER-EVD-012")
    def test_nested_async_decorated_and_module_level_code(self, tmp_path: Path) -> None:
        engine = self._structure(tmp_path, {"src/pkg/service.py": SOURCE})
        s = engine.structure("src/pkg/service.py")
        assert s is not None and s.error is None
        assert s.symbols == (
            Symbol("register", SymbolKind.FUNCTION, 7, 8),
            Symbol("Service", SymbolKind.CLASS, 12, 21),
            Symbol("Service.run", SymbolKind.METHOD, 13, 15),
            Symbol("Service.fetch", SymbolKind.METHOD, 17, 21),
            Symbol("Service.fetch.inner", SymbolKind.FUNCTION, 18, 19),
        )
        assert s.imports == (
            ImportEdge("os", (), 1),
            ImportEdge("numpy", (), 2),
            ImportEdge("pkg.core", ("add",), 3),
            ImportEdge("pkg", ("util",), 4),
        )
        assert s.calls == (
            CallEdge("Service.run", "self.helper", 14, None),
            CallEdge("Service.run", "plus", 15, "pkg.core.add"),
            CallEdge("Service.fetch.inner", "os.path.join", 19, "os.path.join"),
            CallEdge("Service.fetch", "inner", 21, None),
            CallEdge(MODULE_LEVEL, "print", 24, None),
            CallEdge(MODULE_LEVEL, "np.array", 24, "numpy.array"),
            CallEdge(MODULE_LEVEL, "x", 25, None),
        )

    @pytest.mark.req("TER-EVD-012")
    def test_a_file_that_does_not_parse_reports_the_error(self, tmp_path: Path) -> None:
        engine = self._structure(tmp_path, {"src/pkg/bad.py": "def broken(:\n"})
        s = engine.structure("src/pkg/bad.py")
        assert s is not None
        assert (
            s.error is not None
            and s.error.startswith("SyntaxError")
            and "line 1" in s.error
        )
        assert (s.symbols, s.imports, s.calls) == ((), (), ())

    @pytest.mark.req("TER-EVD-012")
    def test_an_unsupported_language_has_no_structure(self, tmp_path: Path) -> None:
        engine = self._structure(tmp_path, {"web/app.js": "function f() { g(); }\n"})
        assert engine.structure("web/app.js") is None
        assert engine.structure("README.md") is None

    @pytest.mark.req("TER-EVD-012")
    def test_the_package_module_is_named_for_its_package(self, tmp_path: Path) -> None:
        s = self._structure(tmp_path, {}).structure("src/pkg/__init__.py")
        assert s is not None and s.module == "pkg" and s.symbols == ()


# -- Git engine ----------------------------------------------------------------


class TestGitWorkTree:
    @pytest.mark.req("TER-EVD-013")
    def test_a_directory_outside_git_fails_clearly(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path / "plain")
        with pytest.raises(NotAWorkTreeError, match="not a Git working tree"):
            GitRepositoryEvidence(root)
        # The engines without version control evidence say so without error.
        assert LexicalRepositoryEvidence(root).diff() is None
        assert PythonSyntaxEvidence(root).history("src/pkg/core.py") is None

    @pytest.mark.req("TER-EVD-013")
    def test_a_subdirectory_of_a_work_tree_is_refused(self, tmp_path: Path) -> None:
        root = git_repo(tmp_path / "repo")
        with pytest.raises(NotAWorkTreeError, match="top level"):
            GitRepositoryEvidence(root / "src")

    @pytest.mark.req("TER-EVD-013")
    def test_a_bare_repository_is_not_a_work_tree(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare.git"
        bare.mkdir()
        git(bare, "init", "-q", "--bare")
        with pytest.raises(NotAWorkTreeError):
            GitRepositoryEvidence(bare)


class TestGitDiff:
    @pytest.mark.req("TER-EVD-013")
    def test_a_clean_tree_has_an_empty_diff_against_head(self, tmp_path: Path) -> None:
        root = git_repo(tmp_path / "repo")
        diff = GitRepositoryEvidence(root).diff()
        assert diff is not None
        assert diff.base == git(root, "rev-parse", "HEAD").strip()
        assert diff.files == ()

    @pytest.mark.req("TER-EVD-013")
    def test_staged_unstaged_deleted_and_untracked_changes(
        self, tmp_path: Path
    ) -> None:
        root = git_repo(tmp_path / "repo", {**REPO, ".gitignore": "*.log\n"})
        (root / "src/pkg/core.py").write_text(
            REPO["src/pkg/core.py"].replace("a + b", "b + a") + "# end\n",
            encoding="utf-8",
        )
        (root / "src/pkg/staged.py").write_text("X = 1\n", encoding="utf-8")
        git(root, "add", "src/pkg/staged.py")
        (root / "tests/helpers.py").unlink()
        (root / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
        (root / "run.log").write_text("ignored\n", encoding="utf-8")
        (root / "data.bin").write_bytes(b"\0\1\2")

        engine = GitRepositoryEvidence(root)
        diff = engine.diff()
        assert diff is not None
        by_path = {c.path: c for c in diff.files}
        assert diff.paths() == (
            "data.bin",
            "notes.txt",
            "src/pkg/core.py",
            "src/pkg/staged.py",
            "tests/helpers.py",
        )
        core = by_path["src/pkg/core.py"]
        assert (core.status, core.added_lines, core.removed_lines) == (
            ChangeStatus.MODIFIED,
            2,
            1,
        )
        assert "+    return b + a" in core.patch and "-    return a + b" in core.patch
        assert by_path["src/pkg/staged.py"].status is ChangeStatus.ADDED
        assert by_path["tests/helpers.py"].status is ChangeStatus.DELETED
        assert by_path["tests/helpers.py"].removed_lines == 1
        notes = by_path["notes.txt"]
        assert (notes.status, notes.added_lines, notes.removed_lines) == (
            ChangeStatus.UNTRACKED,
            2,
            0,
        )
        assert "+two" in notes.patch
        binary = by_path["data.bin"]
        assert (binary.added_lines, binary.removed_lines) == (None, None)
        # Git's view of the files: ignored and deleted files are not listed.
        assert (
            "run.log" not in engine.files() and "tests/helpers.py" not in engine.files()
        )
        assert {"notes.txt", "src/pkg/staged.py"} <= set(engine.files())
        assert engine.diff() == diff

    @pytest.mark.req("TER-EVD-013")
    def test_before_the_first_commit_the_diff_has_no_base(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        git(root, "init", "-q")
        write_repo(root, {"a.py": "A = 1\n", "b.py": "B = 2\n"})
        git(root, "add", "a.py")
        engine = GitRepositoryEvidence(root)
        diff = engine.diff()
        assert diff is not None and diff.base is None
        assert [(c.path, c.status) for c in diff.files] == [
            ("a.py", ChangeStatus.ADDED),
            ("b.py", ChangeStatus.UNTRACKED),
        ]
        assert engine.history("a.py") == ()


class TestGitHistory:
    @pytest.mark.req("TER-EVD-013")
    def test_history_is_newest_first_with_counts_and_follows_renames(
        self, tmp_path: Path
    ) -> None:
        root = git_repo(tmp_path / "repo")
        (root / "src/pkg/core.py").write_text(
            REPO["src/pkg/core.py"] + "\n\ndef sub(a, b):\n    return a - b\n",
            encoding="utf-8",
        )
        git(root, "commit", "-q", "-am", "Add sub", date="2026-01-03T00:00:00+00:00")
        git(root, "mv", "src/pkg/core.py", "src/pkg/maths.py")
        git(root, "commit", "-q", "-m", "Rename core", date="2026-01-04T00:00:00+00:00")
        (root / "src/pkg/util.py").write_text("X = 1\n", encoding="utf-8")
        git(root, "commit", "-q", "-am", "Touch util", date="2026-01-05T00:00:00+00:00")

        engine = GitRepositoryEvidence(root)
        history = engine.history("src/pkg/maths.py")
        assert history is not None
        shas = git(root, "log", "--format=%H").split()
        assert [c.subject for c in history] == ["Rename core", "Add sub", "initial"]
        assert [c.sha for c in history] == [shas[1], shas[2], shas[3]]
        assert [c.path for c in history] == [
            "src/pkg/maths.py",
            "src/pkg/core.py",
            "src/pkg/core.py",
        ]
        assert [(c.added_lines, c.removed_lines) for c in history] == [
            (0, 0),
            (4, 0),
            (7, 0),
        ]
        assert history[0].committed_at == "2026-01-04T00:00:00+00:00"
        assert history[0].author == "Test Author"
        # Every file has its own history; an untracked file has none yet.
        assert [c.subject for c in engine.history("src/pkg/util.py") or ()] == [
            "Touch util",
            "initial",
        ]
        (root / "new.py").write_text("", encoding="utf-8")
        assert engine.history("new.py") == ()


# -- the capability registry (TER-ARC-007) -------------------------------------


@dataclass
class _Entry:
    name: str
    value: str
    obj: object

    def load(self) -> Any:
        return self.obj


class _SingleFile:
    """A third-party repository engine: one file, nothing else."""

    name = "single"

    def __init__(self, root: Path) -> None:
        self.root = root

    def files(self) -> tuple[str, ...]:
        return ("only.py",)

    def text(self, path: str) -> str:
        return ""

    def search(self, needle: str, *, regex: bool = False) -> tuple[Any, ...]:
        return ()

    def tests_importing(self, path: str) -> tuple[str, ...]:
        return ()

    def structure(self, path: str) -> None:
        return None

    def structure_of(self, path: str, text: str) -> None:
        return None

    def diff(self) -> None:
        return None

    def history(self, path: str) -> None:
        return None


class _NoSearch:
    name = "broken"

    def __init__(self, root: Path) -> None:
        self.root = root

    def files(self) -> tuple[str, ...]:
        return ()


class TestRepositoryEnginesArePlugins:
    @pytest.mark.req("TER-ARC-007")
    def test_the_built_in_engines_are_capabilities(self, tmp_path: Path) -> None:
        assert set(default_registry().names("RepositoryEvidence")) >= {
            "git",
            "lexical",
            "python-ast",
        }
        root = git_repo(tmp_path / "repo")
        expected = {
            "lexical": LexicalRepositoryEvidence,
            "git": GitRepositoryEvidence,
            "python-ast": PythonSyntaxEvidence,
        }
        for name, cls in expected.items():
            engine = repository_evidence(root, name)
            assert type(engine) is cls and engine.name == name
            assert isinstance(engine, RepositoryEvidence)
        assert type(repository_evidence(root)) is LexicalRepositoryEvidence

    @pytest.mark.req("TER-ARC-007")
    def test_an_installed_engine_loads_through_the_registry(
        self, tmp_path: Path
    ) -> None:
        registry = CapabilityRegistry(
            discover=lambda: [
                _Entry("RepositoryEvidence.single", "x:Single", _SingleFile)
            ]
        )
        engine = repository_evidence(tmp_path, "single", registry)
        assert isinstance(engine, _SingleFile) and engine.files() == ("only.py",)
        assert registry.problems == ()

    @pytest.mark.req("TER-ARC-007")
    def test_an_engine_that_breaks_the_port_or_is_unknown_is_refused(
        self, tmp_path: Path
    ) -> None:
        registry = CapabilityRegistry(
            discover=lambda: [
                _Entry("RepositoryEvidence.broken", "x:NoSearch", _NoSearch)
            ]
        )
        with pytest.raises(CapabilityError, match="search"):
            repository_evidence(tmp_path, "broken", registry)
        assert any(p.key == "RepositoryEvidence.broken" for p in registry.problems)
        with pytest.raises(UnknownCapabilityError, match="lexical"):
            repository_evidence(tmp_path, "nope", registry)

    @pytest.mark.req("TER-ARC-007")
    def test_engine_errors_reach_the_caller(self, tmp_path: Path) -> None:
        with pytest.raises(NotAWorkTreeError):
            repository_evidence(write_repo(tmp_path / "plain"), "git")

    @pytest.mark.req("TER-ARC-007")
    def test_every_built_in_engine_is_declared_as_an_entry_point(self) -> None:
        engines = {
            k for k in BUILTIN_CAPABILITIES if k.startswith("RepositoryEvidence.")
        }
        assert engines == {
            "RepositoryEvidence.git",
            "RepositoryEvidence.lexical",
            "RepositoryEvidence.python-ast",
        }
        assert CapabilityRegistry(discover=None).check("RepositoryEvidence") == ()
