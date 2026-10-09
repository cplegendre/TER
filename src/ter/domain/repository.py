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
from collections.abc import Collection, Iterable, Mapping
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
    "session_root",
    "source_language",
    "tests_importing",
    "within",
    "ECMASCRIPT_SUFFIXES",
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


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


def _absolute(path: str) -> bool:
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


def session_root(paths: Iterable[str], files: Collection[str]) -> str | None:
    """The directory a session's absolute paths name the repository by.

    A session records the paths its tools used (``/home/me/proj/src/a.py``);
    the repository lists them relative to its root (``src/a.py``). For each
    absolute path, the shortest prefix whose remainder is a listed file is a
    candidate root; the root is the candidate most paths agree on (ties: the
    shorter, then the first in sort order). When no path names a listed
    file (a session that only creates files), the same vote runs on paths
    whose directory is a directory of the repository. ``None`` when neither
    finds a candidate.
    """
    absolute = [p.replace("\\", "/") for p in paths]
    absolute = [p for p in absolute if _absolute(p)]
    directories = {str(PurePosixPath(f).parent) for f in files} - {"."}
    for known, of in ((files, lambda r: r), (directories, _parent)):
        votes: dict[str, int] = {}
        for normal in absolute:
            parts = _parts(normal)
            lead = "/" if normal.startswith("/") else ""
            for cut in range(1, len(parts)):
                if of("/".join(parts[cut:])) in known:
                    root = lead + "/".join(parts[:cut])
                    votes[root] = votes.get(root, 0) + 1
                    break
        if votes:
            return min(votes, key=lambda r: (-votes[r], len(r), r))
    return None


def _parent(path: str) -> str:
    return str(PurePosixPath(path).parent)


def repository_path(path: str, root: str | None) -> str | None:
    """``path`` relative to the repository, or ``None`` when it lies outside.

    A relative path is taken as relative to the repository root (the
    session's working directory). An absolute one must lie under ``root``
    (from :func:`session_root`); with no root, it lies outside.
    """
    normal = path.replace("\\", "/")
    absolute = _absolute(normal)
    parts = _parts(normal)
    if not absolute:
        return None if not parts or ".." in parts else "/".join(parts)
    if root is None:
        return None
    base = _parts(root)
    if len(parts) <= len(base) or parts[: len(base)] != base:
        return None
    rest = parts[len(base) :]
    return None if ".." in rest else "/".join(rest)


# -- architecture contracts ------------------------------------------------------


class ContractFormatError(RepositoryEvidenceError):
    """A declared architecture contract cannot be read."""


class ContractKind(StrEnum):
    """The import-linter contract types TER evaluates."""

    FORBIDDEN = "forbidden"
    LAYERS = "layers"
    INDEPENDENCE = "independence"


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
      under another.

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


def _owner(module: str, candidates: Iterable[str]) -> str | None:
    """The most specific candidate ``module`` lies within."""
    owners = [c for c in candidates if within(module, c)]
    return max(owners, key=len) if owners else None


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
    importer: str, imported: str, contracts: Iterable[ArchitectureContract]
) -> tuple[ContractViolation, ...]:
    """Every contract the import of ``imported`` by ``importer`` breaks.

    Only this one direct import is judged: indirect chains, which
    import-linter also forbids by default, need the whole import graph after
    the session and are not evaluated here.
    """
    out: list[ContractViolation] = []
    for contract in contracts:
        if _ignored(importer, imported, contract.ignore_imports):
            continue
        rules: list[str] = []
        if contract.kind is ContractKind.FORBIDDEN:
            source = _owner(importer, contract.source_modules)
            target = _owner(imported, contract.forbidden_modules)
            if source is not None and target is not None:
                rules.append(f"{source} must not import {target}")
        elif contract.kind is ContractKind.INDEPENDENCE:
            source = _owner(importer, contract.modules)
            target = _owner(imported, contract.modules)
            if source is not None and target is not None and source != target:
                rules.append(f"{source} and {target} must be independent")
        else:
            rules.extend(_layer_violations(contract, importer, imported))
        out.extend(
            ContractViolation(contract.id, contract.name, importer, imported, r)
            for r in rules
        )
    return tuple(out)
