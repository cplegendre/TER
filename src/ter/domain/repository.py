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

import re
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

__all__ = [
    "MODULE_LEVEL",
    "ArchitectureContract",
    "CallEdge",
    "ContractFormatError",
    "ContractKind",
    "ContractViolation",
    "Layer",
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
    "contract_violations",
    "import_candidates",
    "imported_modules",
    "imports_module",
    "is_ecmascript_source",
    "is_python_source",
    "is_test_module",
    "is_vendored",
    "module_name",
    "package_of",
    "repository_path",
    "resolve_import",
    "resolve_relative",
    "canonical_path",
    "has_drive_letter",
    "is_harness_state",
    "session_root",
    "session_roots",
    "source_language",
    "tests_importing",
    "within",
    "ECMASCRIPT_SUFFIXES",
    "HARNESS_DIR",
    "WORKTREES_DIR",
    "VENDOR_DIRS",
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
    #: For a language that imports files by path (TypeScript, JavaScript,
    #: Svelte), the repository paths the import may load, in resolution
    #: order: the first that is a file of the repository is the one it loads
    #: (:func:`resolve_import`). ``module`` is then the specifier as written
    #: (``./util``, ``$lib/api``, ``react``). Empty for a Python import, which
    #: is resolved by module name, and for an import of an external package.
    candidates: tuple[str, ...] = ()


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
    does not parse.

    ``module`` is the name other files import the file by: a dotted name for
    Python, the file's own repository path for a language that imports files
    by path (``language`` ``ecmascript``)."""

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


#: File suffixes whose imports TER reads as ECMAScript modules: TypeScript,
#: JavaScript, and the ``<script>`` blocks of Svelte and Vue components.
ECMASCRIPT_SUFFIXES = frozenset(
    {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".svelte", ".vue"}
)

#: Directories that hold installed third-party packages, not the
#: repository's own source.
VENDOR_DIRS = frozenset({"node_modules"})


def is_ecmascript_source(path: str) -> bool:
    return PurePosixPath(path).suffix in ECMASCRIPT_SUFFIXES


def source_language(path: str) -> str | None:
    """The language TER has import rules for, by file suffix: ``python``,
    ``ecmascript`` (TypeScript, JavaScript, Svelte, Vue), or ``None``."""
    if is_python_source(path):
        return "python"
    if is_ecmascript_source(path):
        return "ecmascript"
    return None


def is_vendored(path: str) -> bool:
    """Whether ``path`` is third-party or built code, never the repository's
    own source: a file in an installed-packages directory (``node_modules``)
    or a minified bundle (``pdf.worker.min.mjs``, ``jquery.min.js``)."""
    parts = path.split("/")
    return ".min." in parts[-1] or any(part in VENDOR_DIRS for part in parts[:-1])


_ES_TEST = re.compile(r"\.(?:test|spec)\.[A-Za-z]+$")
_ES_TEST_DIRS = frozenset({"__tests__", "tests"})


def is_test_module(path: str) -> bool:
    """Whether ``path`` is a test module.

    Python: pytest's default rule, ``test_*.py`` or ``*_test.py``
    (``conftest.py`` and helpers are not tests). TypeScript and JavaScript
    (and Svelte or Vue components): the Jest, Vitest and Playwright
    conventions, ``*.test.*`` or ``*.spec.*``, or any such file under a
    ``__tests__/`` or ``tests/`` directory. Nothing under ``node_modules``.
    """
    p = PurePosixPath(path)
    name = p.name
    if name.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py")
    if p.suffix not in ECMASCRIPT_SUFFIXES or is_vendored(path):
        return False
    if _ES_TEST.search(name):
        return True
    return any(part in _ES_TEST_DIRS for part in p.parts[:-1])


def resolve_import(edge: ImportEdge, files: Collection[str]) -> str | None:
    """The repository file an import that names files by path loads: its
    first candidate that ``files`` holds, or ``None`` (an external package,
    or a file the repository does not have)."""
    return next((c for c in edge.candidates if c in files), None)


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


def within(module: str, package: str) -> bool:
    """Whether ``module`` is ``package`` or a module under it."""
    return module == package or module.startswith(package + ".")


def imported_modules(edge: ImportEdge, modules: Collection[str]) -> tuple[str, ...]:
    """The modules an import statement depends on, as import-linter reads it.

    ``from p import n`` imports the submodule ``p.n`` when ``modules`` (the
    repository's module names) holds it, and otherwise the names of ``p``,
    which is a dependency on ``p``. An import that climbs above the
    top-level package names no module.
    """
    if not edge.module or edge.module.startswith("."):
        return ()
    subs = tuple(
        f"{edge.module}.{n}"
        for n in edge.names
        if n != "*" and f"{edge.module}.{n}" in modules
    )
    whole = not edge.names or len(subs) < len([n for n in edge.names if n != "*"])
    whole = whole or "*" in edge.names
    return ((edge.module,) if whole else ()) + subs


# -- session paths -------------------------------------------------------------

_DRIVE = re.compile(r"([A-Za-z]):(?:/|$)")
_GIT_BASH = re.compile(r"/([A-Za-z])(?:/|$)")

#: The directory Claude Code keeps its own state in (plans, memory,
#: settings), and the subdirectory of it that holds worktree checkouts.
HARNESS_DIR = ".claude"
WORKTREES_DIR = "worktrees"


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


def _absolute(path: str) -> bool:
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


def has_drive_letter(path: str) -> bool:
    """Whether ``path`` is a Windows path with a drive letter (``D:/x``,
    ``d:\\x``)."""
    return _DRIVE.match(path.replace("\\", "/")) is not None


def canonical_path(path: str, windows: bool = False) -> str:
    """One spelling of an absolute path, for matching it against roots
    (TER-EVD-018).

    Backslashes become ``/`` and a drive letter is upper case (``d:\\x`` ->
    ``D:/x``): Windows drive letters are case-insensitive. With ``windows``
    (the session also uses drive-letter paths), a Git-Bash path ``/d/x`` is
    read as ``D:/x``. Nothing else is case-folded: a POSIX path is case
    sensitive, and a one-letter top-level directory (``/d``) is a Git-Bash
    drive only in a session that shows it runs on Windows.
    """
    normal = path.replace("\\", "/")
    if m := _DRIVE.match(normal):
        return m.group(1).upper() + normal[1:]
    if windows and (m := _GIT_BASH.match(normal)):
        return f"{m.group(1).upper()}:{normal[2:] or '/'}"
    return normal


def _lead(normal: str) -> str:
    return "/" if normal.startswith("/") else ""


def _ancestors(path: str, depth: int) -> Iterator[str]:
    """Proper ancestor directories of ``path`` at least ``depth`` deep."""
    parts = path.split("/")[:-1]
    for n in range(len(parts), depth - 1, -1):
        yield "/".join(parts[:n])


def _vote(absolute: Sequence[str], matches: Callable[[str], bool]) -> str | None:
    """The prefix most paths agree on, cutting each path at its shortest
    prefix whose remainder ``matches`` (ties: the shorter, then the first in
    sort order)."""
    votes: dict[str, int] = {}
    for normal in absolute:
        parts = _parts(normal)
        for cut in range(1, len(parts)):
            if matches("/".join(parts[cut:])):
                root = _lead(normal) + "/".join(parts[:cut])
                votes[root] = votes.get(root, 0) + 1
                break
    if not votes:
        return None
    return min(votes, key=lambda r: (-votes[r], len(r), r))


def _worktree(normal: str) -> tuple[str, str] | None:
    """``(checkout, remainder)`` for a path under a Claude Code worktree
    checkout ``<dir>/.claude/worktrees/<name>/...``."""
    parts = _parts(normal)
    for i in range(len(parts) - 3):
        if parts[i] == HARNESS_DIR and parts[i + 1] == WORKTREES_DIR:
            checkout = _lead(normal) + "/".join(parts[: i + 3])
            return checkout, "/".join(parts[i + 3 :])
    return None


def _under(parts: Sequence[str], root: str) -> list[str] | None:
    """The parts of a path below ``root``, or ``None`` when it is not below."""
    base = _parts(root)
    if len(parts) <= len(base) or list(parts[: len(base)]) != base:
        return None
    return list(parts[len(base) :])


def session_roots(paths: Iterable[str], files: Collection[str]) -> tuple[str, ...]:
    """Every directory a session's absolute paths name the repository by,
    the main root first (TER-EVD-006, TER-EVD-017, TER-EVD-018, TER-EVD-020).

    A session records the paths its tools used (``/home/me/proj/src/a.py``);
    the repository lists them relative to its root (``src/a.py``). Paths are
    compared in one spelling (:func:`canonical_path`).

    *Main root*: for each absolute path, the shortest prefix whose remainder
    is a listed file is a candidate; the root is the candidate most paths
    agree on (ties: the shorter, then the first in sort order). When no path
    names a listed file (a session that only creates files), the same vote
    runs on paths whose directory is a directory of the repository, and then
    on paths that lie under a repository directory at least two levels deep
    (``src/main/java/...`` for a new package of new files). One level is not
    enough: a top-level name such as ``src`` also names directories *above*
    checkouts (``/home/me/src/proj``), so it alone never makes a root.

    *Other roots*: a Claude Code worktree checkout
    (``<dir>/.claude/worktrees/<name>``) when the remainder of a path under
    it is a listed file or a repository directory, or lies in one; the
    directory that worktree was made from (``<dir>``), when the remainder of
    a path under it, outside its ``.claude``, does the same; and, for paths
    under no root yet, a prefix under which at least two distinct
    paths name listed files, at least one of them in a subdirectory, so a
    stray ``README.md`` or ``package.json`` elsewhere does not make a root.
    Other roots follow the main root, in sort order.
    """
    raw = [p.replace("\\", "/") for p in paths]
    raw = [p for p in raw if _absolute(p)]
    windows = any(has_drive_letter(p) for p in raw)
    absolute = list(dict.fromkeys(canonical_path(p, windows) for p in raw))
    directories = {str(PurePosixPath(f).parent) for f in files} - {"."}

    def listed(rest: str) -> bool:
        return rest in files

    def in_directory(rest: str) -> bool:
        return _parent(rest) in directories

    def under_directory(rest: str) -> bool:
        return any(a in directories for a in _ancestors(rest, 2))

    def names_repository(rest: str) -> bool:
        return rest in files or rest in directories or in_directory(rest)

    main: str | None = None
    for matches in (listed, in_directory, under_directory):
        main = _vote(absolute, matches)
        if main is not None:
            break
    others: set[str] = set()
    for normal in absolute:
        found = _worktree(normal)
        if found is None:
            continue
        checkout, rest = found
        if names_repository(rest):
            others.add(checkout)
    # The directory a worktree was made from is a checkout too, when a path
    # under it (outside its ``.claude``) names the repository the same way.
    owners = {w.rsplit(f"/{HARNESS_DIR}/{WORKTREES_DIR}/", 1)[0] for w in others} - {""}
    for normal in absolute:
        parts = _parts(normal)
        for owner in owners - others:
            rest_parts = _under(parts, owner)
            if (
                rest_parts is not None
                and rest_parts[0] != HARNESS_DIR
                and names_repository("/".join(rest_parts))
            ):
                others.add(owner)
    accepted = [r for r in (main, *others) if r is not None]
    named: dict[str, set[str]] = {}
    for normal in absolute:
        parts = _parts(normal)
        if any(_under(parts, r) is not None for r in accepted):
            continue
        for cut in range(1, len(parts)):
            rest = "/".join(parts[cut:])
            if rest in files:
                named.setdefault(_lead(normal) + "/".join(parts[:cut]), set()).add(rest)
                break
    others.update(
        root
        for root, rests in named.items()
        if len(rests) >= 2 and any("/" in r for r in rests)
    )
    if main is not None:
        others.discard(main)
    return ((main,) if main is not None else ()) + tuple(sorted(others))


def session_root(paths: Iterable[str], files: Collection[str]) -> str | None:
    """The main directory a session's absolute paths name the repository
    by: the first of :func:`session_roots`, or ``None``."""
    roots = session_roots(paths, files)
    return roots[0] if roots else None


def _parent(path: str) -> str:
    return str(PurePosixPath(path).parent)


def repository_path(path: str, root: str | Sequence[str] | None) -> str | None:
    """``path`` relative to the repository, or ``None`` when it lies outside.

    A relative path is taken as relative to the repository root (the
    session's working directory). An absolute one must lie under ``root``
    (one root, or the roots from :func:`session_roots`); under more than
    one, the deepest decides (a worktree checkout inside the main
    checkout). With no root, it lies outside. Drive letters match in any
    case, and a Git-Bash path ``/d/x`` matches ``D:/x`` when a root is
    spelled with a drive letter (:func:`canonical_path`).
    """
    normal = path.replace("\\", "/")
    parts = _parts(normal)
    if not _absolute(normal):
        return None if not parts or ".." in parts else "/".join(parts)
    roots = () if root is None else (root,) if isinstance(root, str) else tuple(root)
    windows = any(has_drive_letter(r) for r in roots)
    parts = _parts(canonical_path(normal, windows))
    best: list[str] | None = None
    for r in roots:
        rest = _under(parts, canonical_path(r, windows))
        if rest is not None and (best is None or len(rest) < len(best)):
            best = rest
    return None if best is None or ".." in best else "/".join(best)


def is_harness_state(path: str) -> bool:
    """Whether ``path`` lies under a ``.claude`` directory that is not a
    worktree checkout: the agent harness's plans, memory and settings
    (``~/.claude/plans/x.md``, ``~/.claude/CLAUDE.md``), not repository
    code (TER-EVD-019). Callers ask only about paths outside every root, so
    a project's own ``.claude/`` files inside the repository stay
    repository paths."""
    parts = _parts(path)
    i = 0
    while i < len(parts) - 1:
        if parts[i] == HARNESS_DIR:
            if parts[i + 1] != WORKTREES_DIR:
                return True
            i += 3  # past ``.claude/worktrees/<name>``
            continue
        i += 1
    return False


# -- architecture contracts ------------------------------------------------------


class ContractFormatError(RepositoryEvidenceError):
    """A declared architecture contract cannot be read."""


class ContractKind(StrEnum):
    """The contract types TER evaluates: import-linter's (on Python module
    names) and dependency-cruiser's ``forbidden`` path rules (on repository
    paths, :attr:`by_path`)."""

    FORBIDDEN = "forbidden"
    LAYERS = "layers"
    INDEPENDENCE = "independence"
    # TER-EVD-015: the import-linter types TER-EVD-007 left out, and a
    # second contract source for TypeScript and JavaScript repositories.
    PROTECTED = "protected"
    ACYCLIC_SIBLINGS = "acyclic_siblings"
    PATH_FORBIDDEN = "path_forbidden"

    @property
    def by_path(self) -> bool:
        """Whether the contract names files by repository path (a
        TypeScript, JavaScript, Svelte or Vue import), not Python modules."""
        return self is ContractKind.PATH_FORBIDDEN


@dataclass(frozen=True)
class Layer:
    """One layer of a ``layers`` contract: one module, or sibling modules.

    Siblings written ``a | b`` are *independent*: neither may import the
    other. Siblings written ``a : b`` may import each other.
    """

    modules: tuple[str, ...]
    independent: bool = False


@dataclass(frozen=True)
class ArchitectureContract:
    """A declared rule about which modules may import which (import-linter
    style). Every module name is absolute; a contract about ``p`` covers
    every module under ``p``.

    * ``forbidden``: no module under ``source_modules`` imports a module
      under ``forbidden_modules``;
    * ``layers``: ``layers`` run from highest to lowest; a lower layer never
      imports a higher one. With ``containers``, the layers are the
      containers' children (``<container>.<layer>``);
    * ``independence``: no module under one of ``modules`` imports a module
      under another;
    * ``protected``: a module under ``protected_modules`` is imported only
      by modules under ``allowed_importers`` or under the same protected
      module;
    * ``acyclic_siblings``: within each package under ``ancestors`` (down
      to ``depth`` levels, not below a ``skip_descendants`` module), the
      children's imports of each other form no cycle;
    * ``path_forbidden`` (dependency-cruiser): no file whose repository
      path matches a ``from_paths`` regular expression (and no
      ``from_paths_not`` one) imports a file whose path matches a
      ``to_paths`` one (and no ``to_paths_not`` one). ``$1`` ... ``$9`` in a
      ``to`` expression stand for the groups the ``from`` path matched.

    With ``as_packages`` false, ``forbidden`` and ``protected`` module
    names stand for those modules alone, not for the modules under them.
    ``ignore_imports`` are ``importer -> imported`` patterns exempt from the
    contract; ``*`` stands for one module name part, ``**`` for any number.
    """

    id: str
    name: str
    kind: ContractKind
    source_modules: tuple[str, ...] = ()
    forbidden_modules: tuple[str, ...] = ()
    layers: tuple[Layer, ...] = ()
    containers: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    ignore_imports: tuple[str, ...] = ()
    #: Where the contract was declared (a repository path).
    source: str = ""
    protected_modules: tuple[str, ...] = ()
    allowed_importers: tuple[str, ...] = ()
    as_packages: bool = True
    ancestors: tuple[str, ...] = ()
    depth: int = 10
    skip_descendants: tuple[str, ...] = ()
    from_paths: tuple[str, ...] = ()
    from_paths_not: tuple[str, ...] = ()
    to_paths: tuple[str, ...] = ()
    to_paths_not: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractViolation:
    """One import that breaks one contract, and the rule it breaks."""

    contract: str
    contract_name: str
    importer: str
    imported: str
    rule: str


def _pattern(text: str) -> re.Pattern[str]:
    parts = []
    for part in text.strip().split("."):
        if part == "**":
            parts.append(r"[^.]+(?:\.[^.]+)*")
        elif part == "*":
            parts.append(r"[^.]+")
        else:
            parts.append(re.escape(part))
    return re.compile(r"\.".join(parts) + r"\Z")


def _ignored(importer: str, imported: str, patterns: Iterable[str]) -> bool:
    for written in patterns:
        left, arrow, right = written.partition("->")
        if not arrow:
            continue
        if _pattern(left).match(importer) and _pattern(right).match(imported):
            return True
    return False


def _owner(
    module: str, candidates: Iterable[str], as_packages: bool = True
) -> str | None:
    """The most specific candidate ``module`` lies within (is, when not
    ``as_packages``)."""
    owners = [
        c for c in candidates if (within(module, c) if as_packages else module == c)
    ]
    return max(owners, key=len) if owners else None


def _protected_violation(
    contract: ArchitectureContract, importer: str, imported: str
) -> str | None:
    """import-linter ``protected``: only allowed importers (and the
    protected module itself) import a protected module. Like import-linter,
    only direct imports are judged."""
    as_packages = contract.as_packages
    target = _owner(imported, contract.protected_modules, as_packages)
    if target is None:
        return None
    if _owner(importer, (target,), as_packages) is not None:
        return None  # inside the protected package
    if _owner(importer, contract.allowed_importers, as_packages) is not None:
        return None
    allowed = ", ".join(contract.allowed_importers) or "no module"
    return f"{target} is protected: only {allowed} may import it"


def _sibling_package(
    contract: ArchitectureContract, importer: str, imported: str
) -> tuple[str, str, str] | None:
    """``(package, importer's child, imported's child)`` when the import
    links two children of a package the ``acyclic_siblings`` contract
    covers: the deepest package both lie under, within an ancestor, at most
    ``depth`` levels below it, and not within a skipped descendant."""
    a, b = importer.split("."), imported.split(".")
    common = 0
    while common < min(len(a), len(b)) and a[common] == b[common]:
        common += 1
    if common >= len(a) or common >= len(b) or common == 0:
        return None  # one contains the other, or no common package
    package = ".".join(a[:common])
    ancestor = _owner(package, contract.ancestors)
    if ancestor is None:
        return None
    if common - (ancestor.count(".") + 1) > contract.depth:
        return None
    skipped = [s for s in contract.skip_descendants if within(package, s)]
    if any(s != ancestor for s in skipped):
        return None
    return package, ".".join(a[: common + 1]), ".".join(b[: common + 1])


def _cycle_violation(
    contract: ArchitectureContract,
    importer: str,
    imported: str,
    graph: Mapping[str, Collection[str]] | None,
) -> str | None:
    """import-linter ``acyclic_siblings``, judged for one new import: it
    breaks the contract when it adds a dependency between two children of a
    covered package, and the imported child already depends, directly or
    through other children, on the importing one (``graph``: every module's
    direct imports before this one). A cycle that was there before is not
    this import's doing."""
    if graph is None:
        return None
    found = _sibling_package(contract, importer, imported)
    if found is None:
        return None
    package, source, target = found
    depth = package.count(".") + 2  # parts in a child's name
    edges: dict[str, set[str]] = {}
    for module, targets in graph.items():
        if not within(module, package) or module == package:
            continue
        child = ".".join(module.split(".")[:depth])
        for t in targets:
            if within(t, package) and t != package and not within(t, child):
                if not _ignored(module, t, contract.ignore_imports):
                    edges.setdefault(child, set()).add(".".join(t.split(".")[:depth]))
    if target in edges.get(source, ()):
        return None  # the dependency was already there
    path = _path_between(edges, target, source)
    if path is None:
        return None
    chain = " -> ".join([source, *path])
    return (
        f"the children of {package} must not import each other in a cycle, "
        f"and this import closes one: {chain}"
    )


def _path_between(
    edges: Mapping[str, Collection[str]], start: str, goal: str
) -> list[str] | None:
    """The shortest chain of edges from ``start`` to ``goal`` (both ends
    included), or ``None``."""
    previous: dict[str, str | None] = {start: None}
    queue = [start]
    for node in queue:
        if node == goal:
            chain = [node]
            while (step := previous[chain[-1]]) is not None:
                chain.append(step)
            return chain[::-1]
        for nxt in sorted(edges.get(node, ())):
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    return None


def _substitute(pattern: str, groups: Sequence[str | None]) -> str:
    """dependency-cruiser group matching: ``$1`` ... ``$9`` in a ``to``
    expression stand for what the ``from`` expression's groups matched."""

    def group(m: re.Match[str]) -> str:
        n = int(m.group(1))
        value = groups[n - 1] if n <= len(groups) else None
        return re.escape(value or "")

    return re.sub(r"\$([1-9])", group, pattern)


def _path_violation(
    contract: ArchitectureContract, importer: str, imported: str
) -> str | None:
    """dependency-cruiser ``forbidden``: a rule on ``from.path``/
    ``from.pathNot`` and ``to.path``/``to.pathNot`` regular expressions
    (searched, as JavaScript's ``RegExp.test`` does)."""
    groups: Sequence[str | None] = ()
    if contract.from_paths:
        match = next(
            (m for p in contract.from_paths if (m := re.search(p, importer))), None
        )
        if match is None:
            return None
        groups = match.groups()
    if any(re.search(p, importer) for p in contract.from_paths_not):
        return None
    to = [_substitute(p, groups) for p in contract.to_paths]
    if to and not any(re.search(p, imported) for p in to):
        return None
    if any(re.search(_substitute(p, groups), imported) for p in contract.to_paths_not):
        return None
    return f"{importer} must not import {imported}"


def _layer_violations(
    contract: ArchitectureContract, importer: str, imported: str
) -> Iterable[str]:
    containers = contract.containers or ("",)
    for container in containers:
        prefix = f"{container}." if container else ""
        layers = [
            Layer(tuple(prefix + m for m in layer.modules), layer.independent)
            for layer in contract.layers
        ]
        rank = {m: i for i, layer in enumerate(layers) for m in layer.modules}
        source = _owner(importer, rank)
        target = _owner(imported, rank)
        if source is None or target is None or source == target:
            continue
        if rank[target] < rank[source]:
            yield (
                f"{source} is a lower layer than {target}, and a lower layer "
                "must not import a higher one"
            )
        elif rank[target] == rank[source] and layers[rank[source]].independent:
            yield (
                f"{source} and {target} are independent siblings in one layer "
                "and must not import each other"
            )


def contract_violations(
    importer: str,
    imported: str,
    contracts: Iterable[ArchitectureContract],
    *,
    by_path: bool = False,
    graph: Mapping[str, Collection[str]] | None = None,
) -> tuple[ContractViolation, ...]:
    """Every contract the import of ``imported`` by ``importer`` breaks.

    ``importer`` and ``imported`` are Python module names, or with
    ``by_path`` repository paths (a TypeScript, JavaScript, Svelte or Vue
    import); only the contracts of that kind (:attr:`ContractKind.by_path`)
    are judged. ``graph`` (module -> modules it imports directly, before this
    import) is what an ``acyclic_siblings`` contract is judged against;
    without it that contract is not judged.

    Only this one direct import is judged: indirect chains, which
    import-linter also forbids by default, need the whole import graph after
    the session and are not evaluated here.
    """
    out: list[ContractViolation] = []
    for contract in contracts:
        if contract.kind.by_path != by_path:
            continue
        if _ignored(importer, imported, contract.ignore_imports):
            continue
        rules: list[str] = []
        rule: str | None = None
        if contract.kind is ContractKind.FORBIDDEN:
            as_packages = contract.as_packages
            source = _owner(importer, contract.source_modules, as_packages)
            target = _owner(imported, contract.forbidden_modules, as_packages)
            if source is not None and target is not None:
                rules.append(f"{source} must not import {target}")
        elif contract.kind is ContractKind.PROTECTED:
            rule = _protected_violation(contract, importer, imported)
        elif contract.kind is ContractKind.ACYCLIC_SIBLINGS:
            rule = _cycle_violation(contract, importer, imported, graph)
        elif contract.kind is ContractKind.PATH_FORBIDDEN:
            rule = _path_violation(contract, importer, imported)
        elif contract.kind is ContractKind.INDEPENDENCE:
            source = _owner(importer, contract.modules)
            target = _owner(imported, contract.modules)
            if source is not None and target is not None and source != target:
                rules.append(f"{source} and {target} must be independent")
        else:
            rules.extend(_layer_violations(contract, importer, imported))
        if rule is not None:
            rules.append(rule)
        out.extend(
            ContractViolation(contract.id, contract.name, importer, imported, r)
            for r in rules
        )
    return tuple(out)
