"""The lexical repository evidence engine: text only, deterministic.

Reads the files under one root (never following symbolic links, never
entering a version control directory) and answers from their text. Every
answer depends only on the repository's paths and bytes: not on where the
root is, file times or directory listing order (TER-EVD-002).

Test-to-source links come from import statements read as text: ``import a.b``
and ``from a import b`` (parenthesised and continued lines included), with
relative imports resolved against the test module's package. Being lexical,
it also counts an import statement written inside a string; the syntax-tree
engine does not. Imports made by calling ``importlib`` are not import
statements and are never seen.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterator
from pathlib import Path, PurePosixPath

from ....domain.repository import (
    FileCommit,
    ImportEdge,
    RepositoryDiff,
    RepositoryEvidenceError,
    SourceStructure,
    TextMatch,
    UnknownPathError,
    UnsupportedLanguageError,
    import_candidates,
    is_python_source,
    is_test_module,
    module_name,
    package_of,
    resolve_relative,
    tests_importing,
)

__all__ = ["VCS_DIRS", "LexicalRepositoryEvidence", "lexical_imports"]

#: Directories that hold version control state, not repository content.
VCS_DIRS = frozenset({".git", ".hg", ".svn"})

_IMPORT = re.compile(r"^[ \t]*import[ \t]+(?P<modules>[^\n#;]+)", re.MULTILINE)
_FROM = re.compile(
    r"^[ \t]*from[ \t]+(?P<module>\.*[\w.]*)[ \t]+import[ \t]+"
    r"(?P<names>\([^)]*\)|[^\n#;]+)",
    re.MULTILINE,
)
_COMMENT = re.compile(r"#[^\n]*")
_DOTTED = re.compile(r"^\.*[A-Za-z_][\w.]*$|^\*$")


def _first_word(item: str) -> str:
    """``a.b as c`` -> ``a.b``."""
    words = item.split()
    return words[0] if words else ""


def lexical_imports(text: str, package: str) -> tuple[ImportEdge, ...]:
    """Import statements found in ``text`` by pattern, relative ones resolved
    against ``package``. Lines are 1-based, counted in ``text``."""
    edges: list[tuple[int, ImportEdge]] = []
    # A backslash continuation becomes spaces of the same length, so a match
    # offset in ``flat`` is the same offset in ``text``.
    flat = text.replace("\\\r\n", "   ").replace("\\\n", "  ")

    def line_of(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    for match in _IMPORT.finditer(flat):
        for item in match.group("modules").split(","):
            name = _first_word(item)
            if name and _DOTTED.match(name) and not name.startswith("."):
                edges.append(
                    (match.start(), ImportEdge(name, (), line_of(match.start())))
                )
    for match in _FROM.finditer(flat):
        raw = _COMMENT.sub("", match.group("names")).strip().strip("()")
        names = tuple(
            n
            for n in (_first_word(i) for i in raw.split(","))
            if n and _DOTTED.match(n)
        )
        written = match.group("module")
        level = len(written) - len(written.lstrip("."))
        module = resolve_relative(level, written[level:] or None, package)
        edges.append((match.start(), ImportEdge(module, names, line_of(match.start()))))
    return tuple(edge for _, edge in sorted(edges, key=lambda e: e[0]))


class LexicalRepositoryEvidence:
    """A :class:`~ter.ports.driven.RepositoryEvidence` that reads text only."""

    name = "lexical"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise RepositoryEvidenceError(f"{root} is not a directory")

    # -- files -------------------------------------------------------------

    def files(self) -> tuple[str, ...]:
        found: list[str] = []
        for directory, subdirs, names in os.walk(self.root, followlinks=False):
            subdirs[:] = [d for d in subdirs if d not in VCS_DIRS]
            base = Path(directory)
            for name in names:
                full = base / name
                if full.is_symlink() or not full.is_file():
                    continue
                found.append(full.relative_to(self.root).as_posix())
        return tuple(sorted(found))

    def _listed(self, path: str) -> bool:
        """Whether :meth:`files` lists ``path``, decided without walking the
        tree: a relative ``/``-separated path to a regular file, through no
        symbolic link and no version control directory."""
        parts = path.split("/")
        if not path or path.startswith("/") or "\\" in path:
            return False
        if any(part in ("", ".", "..") for part in parts):
            return False
        if any(part in VCS_DIRS for part in parts[:-1]):
            return False
        full = self.root
        for part in parts:
            full = full / part
            if full.is_symlink():
                return False
        return full.is_file()

    def listing(self, extra: str | None = None) -> Collection[str]:
        """:meth:`files` as a collection whose membership test costs no walk
        (``extra`` counts as listed too: a file a session creates)."""
        return _Listing(self, extra)

    def _known(self, path: str) -> Path:
        """The file ``path`` names, or :class:`UnknownPathError`."""
        if not self._listed(path):
            raise UnknownPathError(f"{path!r} is not a file of the repository")
        return self.root / PurePosixPath(path)

    def _read(self, path: str) -> str | None:
        """The file's text, or ``None`` when it is not UTF-8 text."""
        data = (self.root / PurePosixPath(path)).read_bytes()
        if b"\0" in data:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def text(self, path: str) -> str:
        self._known(path)
        text = self._read(path)
        if text is None:
            raise RepositoryEvidenceError(f"{path} is not a UTF-8 text file")
        return text

    # -- search ------------------------------------------------------------

    def search(self, needle: str, *, regex: bool = False) -> tuple[TextMatch, ...]:
        if not needle:
            raise RepositoryEvidenceError("search needs a non-empty needle")
        try:
            pattern = re.compile(needle if regex else re.escape(needle))
        except re.error as exc:
            raise RepositoryEvidenceError(f"invalid pattern {needle!r}: {exc}") from exc
        matches: list[TextMatch] = []
        for path in self.files():
            text = self._read(path)
            if text is None:
                continue
            for number, line in enumerate(_lines(text), start=1):
                if pattern.search(line):
                    matches.append(TextMatch(path, number, line))
        return tuple(matches)

    # -- test-to-source ----------------------------------------------------

    def _imports(
        self, path: str, text: str, files: frozenset[str]
    ) -> tuple[ImportEdge, ...]:
        """The import statements of a Python file (the engine's own reading)."""
        return lexical_imports(text, package_of(path, files))

    def tests_importing(self, path: str) -> tuple[str, ...]:
        self._known(path)
        if not is_python_source(path):
            raise UnsupportedLanguageError(f"{path} is not a Python module")
        files = frozenset(self.files())
        module = module_name(path, files)
        imports: dict[str, tuple[str, ...]] = {}
        for candidate in sorted(files):
            if not is_test_module(candidate) or not is_python_source(candidate):
                continue
            text = self._read(candidate)
            if text is None:
                continue
            imports[candidate] = tuple(
                name
                for edge in self._imports(candidate, text, files)
                for name in import_candidates(edge)
            )
        return tests_importing(module, imports)

    # -- deeper evidence: not available lexically --------------------------

    def structure(self, path: str) -> SourceStructure | None:
        self._known(path)
        return None

    def structure_of(self, path: str, text: str) -> SourceStructure | None:
        return None

    def diff(self) -> RepositoryDiff | None:
        return None

    def history(self, path: str) -> tuple[FileCommit, ...] | None:
        self._known(path)
        return None


class _Listing(Collection[str]):
    """An engine's file list: membership by a direct check, iteration by
    the full listing."""

    def __init__(self, engine: LexicalRepositoryEvidence, extra: str | None) -> None:
        self._engine = engine
        self._extra = extra

    def __contains__(self, path: object) -> bool:
        if not isinstance(path, str):
            return False
        return path == self._extra or self._engine._listed(path)

    def _all(self) -> list[str]:
        files = list(self._engine.files())
        if self._extra is not None and self._extra not in files:
            files.append(self._extra)
        return files

    def __iter__(self) -> Iterator[str]:
        return iter(self._all())

    def __len__(self) -> int:
        return len(self._all())


def _lines(text: str) -> list[str]:
    """Lines as Git and editors number them: split on ``\\n``, ``\\r`` dropped."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]
