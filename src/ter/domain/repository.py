"""Repository evidence: what a repository says about the code a task touches.

These are the values the :class:`~ter.ports.driven.RepositoryEvidence` port
returns (L3 Grounded, ``docs/ter4/l3-grounded.md``). They hold repository
facts only (paths, lines, symbols, imports, calls, diffs and history), never
model output, so the evidence layer depends on no model provider (P054).

Every path is relative to the repository root and written with ``/``, so two
checkouts of the same content yield equal evidence wherever they live
(TER-EVD-002). Every tuple is sorted or in source order, as documented on its
field.

The module also holds the pure rules shared by every engine and the
in-memory fake: which files are test modules, a Python file's dotted module
name, how a relative import resolves, and which test modules import a given
source module (TER-EVD-003). No IO happens here: callers pass in what they
read.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

__all__ = [
    "MODULE_LEVEL",
    "CallEdge",
    "ChangeStatus",
    "FileChange",
    "FileCommit",
    "ImportEdge",
    "NotAWorkTreeError",
    "RepositoryDiff",
    "RepositoryEvidenceError",
    "SourceStructure",
    "Symbol",
    "SymbolKind",
    "TextMatch",
    "UnknownPathError",
    "UnsupportedLanguageError",
    "import_candidates",
    "imports_module",
    "is_python_source",
    "is_test_module",
    "module_name",
    "package_of",
    "resolve_relative",
    "tests_importing",
]

MODULE_LEVEL = "<module>"
"""The ``caller`` of a call made outside any function or class body."""


class RepositoryEvidenceError(ValueError):
    """Repository evidence cannot be produced for the request as given."""


class UnknownPathError(RepositoryEvidenceError):
    """A path names no file of the repository."""


class UnsupportedLanguageError(RepositoryEvidenceError):
    """The engine has no rule for the language of the file asked about."""


class NotAWorkTreeError(RepositoryEvidenceError):
    """A Git-backed engine was pointed at a directory that is not the top
    level of a Git working tree."""


# -- lexical evidence ----------------------------------------------------------


@dataclass(frozen=True, order=True)
class TextMatch:
    """One line of one file that contains the searched text."""

    path: str
    line: int  # 1-based
    text: str  # the whole line, without its line ending


# -- syntax-tree evidence ------------------------------------------------------


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


@dataclass(frozen=True)
class Symbol:
    """A definition in a source file.

    ``name`` is qualified within the module (``Calc.total`` for a method,
    ``outer.inner`` for a nested function).
    """

    name: str
    kind: SymbolKind
    line: int
    end_line: int


@dataclass(frozen=True)
class ImportEdge:
    """One import statement's dependency on a module.

    ``module`` is absolute: a relative import is resolved against the
    importing file's package, and keeps its leading dots only when it climbs
    above the top-level package. ``names`` are the imported names of a
    ``from`` import, in statement order (empty for ``import x``).
    """

    module: str
    names: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class CallEdge:
    """A call from a definition (or the module body) to a dotted name.

    ``callee`` is the called expression as written (``self.helper``,
    ``os.path.join``). ``resolved`` is its absolute dotted name when the head
    of ``callee`` is a name the module imports or defines at top level, and
    ``None`` otherwise.
    """

    caller: str
    callee: str
    line: int
    resolved: str | None = None


@dataclass(frozen=True)
class SourceStructure:
    """What a file's syntax tree says: definitions, imports and calls, each in
    source order. ``error`` is set (and the tuples are empty) when the file
    does not parse."""

    path: str
    language: str
    module: str
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[ImportEdge, ...] = ()
    calls: tuple[CallEdge, ...] = ()
    error: str | None = None


# -- change evidence -----------------------------------------------------------


class ChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNTRACKED = "untracked"
    TYPE_CHANGED = "type-changed"


@dataclass(frozen=True)
class FileChange:
    """One file's change in the working tree against ``RepositoryDiff.base``.

    Line counts are ``None`` for a binary file. ``patch`` is the unified diff
    of the file.
    """

    path: str
    status: ChangeStatus
    added_lines: int | None
    removed_lines: int | None
    patch: str


@dataclass(frozen=True)
class RepositoryDiff:
    """The working tree (staged, unstaged and untracked files) against
    ``base``: the commit ``HEAD`` names, or ``None`` before the first commit.
    ``files`` is sorted by path."""

    base: str | None
    files: tuple[FileChange, ...] = field(default=())

    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


@dataclass(frozen=True)
class FileCommit:
    """One commit in a file's history (newest first). ``path`` is the file's
    name in that commit, which differs from today's after a rename."""

    sha: str
    committed_at: str  # ISO 8601, as the commit records it
    author: str
    subject: str
    path: str
    added_lines: int | None
    removed_lines: int | None


# -- shared rules --------------------------------------------------------------


def is_python_source(path: str) -> bool:
    return PurePosixPath(path).suffix == ".py"


def is_test_module(path: str) -> bool:
    """pytest's default rule: ``test_*.py`` or ``*_test.py``."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def module_name(path: str, files: Collection[str]) -> str:
    """The dotted name Python imports ``path`` by.

    Parent directories count as packages while they hold an ``__init__.py``
    (in ``files``); the first directory without one is an import root
    (``src/``, ``tests/``, the repository root). Namespace packages are not
    recognised.
    """
    if not is_python_source(path):
        raise UnsupportedLanguageError(f"{path} is not a Python module")
    p = PurePosixPath(path)
    names = [] if p.name == "__init__.py" else [p.stem]
    d = p.parent
    while d != PurePosixPath(".") and str(d / "__init__.py") in files:
        names.insert(0, d.name)
        d = d.parent
    return ".".join(names)


def package_of(path: str, files: Collection[str]) -> str:
    """The package a module's relative imports resolve against."""
    name = module_name(path, files)
    if PurePosixPath(path).name == "__init__.py":
        return name
    return name.rpartition(".")[0]


def resolve_relative(level: int, module: str | None, package: str) -> str:
    """Resolve ``from <level dots><module> import ...`` inside ``package``.

    An import that climbs above the top-level package cannot be resolved and
    is returned as written, dots included.
    """
    written = "." * level + (module or "")
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    if level - 1 >= len(parts):
        return written
    base = parts[: len(parts) - (level - 1)]
    return ".".join([*base, *([module] if module else [])])


def import_candidates(edge: ImportEdge) -> tuple[str, ...]:
    """Every module an import statement may load: the module itself and, for
    ``from m import a``, the possible submodule ``m.a``."""
    if not edge.module or edge.module.startswith("."):
        return ()
    return (edge.module,) + tuple(f"{edge.module}.{n}" for n in edge.names if n != "*")


def imports_module(imported: str, module: str) -> bool:
    """Whether importing ``imported`` loads ``module``: Python imports every
    parent package of a dotted name first."""
    return imported == module or imported.startswith(module + ".")


def tests_importing(
    module: str, imports: Mapping[str, Iterable[str]]
) -> tuple[str, ...]:
    """The test modules (keys of ``imports``, by :func:`is_test_module`)
    whose imported modules include ``module``, sorted by path.

    ``imports`` maps a file to every module its import statements may load
    (:func:`import_candidates`).
    """
    return tuple(
        sorted(
            path
            for path, imported in imports.items()
            if is_test_module(path) and any(imports_module(i, module) for i in imported)
        )
    )
