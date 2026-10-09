"""Repository grounding: what the repository says about one session (L3).

A :class:`RepositoryGrounding` is computed once, before analysis, from the
repository at the session's start commit (through the ``RepositoryEvidence``
port) and the session's own edit requests. It holds only values, so the
grounded detectors (:mod:`.surface`) read it like any other index and the
analysis stays a fold over events with no IO in the domain
(``docs/ter4/l3-grounded.md``).

What it holds:

* the repository's files, Python module names, and the **import graph** at
  the start commit (``links``: which repository files each file imports, and
  the reverse, ``importers``), read from syntax trees;
* the **distinctive symbols** each file defines (names a prompt can only mean
  one way: ``tests_importing``, ``ExplainSession``);
* the mapping from the paths the session's tools used to repository paths;
* for every edit or write, the file **after** the edit, replayed from the
  start commit's text, and the imports its syntax tree holds then
  (:class:`EditGrounding`);
* the architecture contracts the repository declares.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..events import EventId
from ..repository import ArchitectureContract

__all__ = [
    "AddedImport",
    "EditGrounding",
    "RepositoryGrounding",
    "distinctive",
    "replay_edit",
]


@dataclass(frozen=True, order=True)
class AddedImport:
    """A module the file imports after an edit and did not import before it."""

    module: str
    line: int


@dataclass(frozen=True)
class EditGrounding:
    """What the repository says about one edit or write request.

    ``path`` is the repository path the request changes. ``created`` is true
    for a file the start commit does not hold. ``applied`` is false when the
    edit could not be replayed on the file as known (its old text was not
    there, or the file's text was already unknown); then nothing after it is
    known either. ``parsed`` is true when the file after the edit has a
    syntax tree; only then are ``imports``, ``links`` and ``added`` read.
    ``imports`` are the modules the file imports after the edit; ``links``
    the repository files those are; ``added`` the imports this edit added.
    """

    event_id: EventId
    path: str
    module: str | None
    created: bool
    applied: bool
    parsed: bool
    imports: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    added: tuple[AddedImport, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "path": self.path,
            "module": self.module,
            "created": self.created,
            "applied": self.applied,
            "parsed": self.parsed,
            "imports": list(self.imports),
            "links": list(self.links),
            "added": [{"module": a.module, "line": a.line} for a in self.added],
        }


@dataclass(frozen=True)
class RepositoryGrounding:
    """The repository evidence one session's analysis is grounded on."""

    #: The ``RepositoryEvidence`` engine the evidence came from.
    engine: str
    #: Whether the engine read syntax trees, so the import graph is known.
    syntax: bool
    files: frozenset[str]
    #: Python file -> dotted module name, for start files and created files.
    modules: Mapping[str, str] = field(default_factory=dict)
    #: File -> repository files it imports at the start commit.
    links: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: File -> repository files that import it at the start commit.
    importers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Distinctive symbol name (lower case) -> files that define it.
    symbols: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The directory the session's absolute paths name the repository by.
    root: str | None = None
    #: Session path -> repository path, ``None`` for a path outside it.
    paths: Mapping[str, str | None] = field(default_factory=dict)
    edits: Mapping[EventId, EditGrounding] = field(default_factory=dict)
    contracts: tuple[ArchitectureContract, ...] = ()
    #: The repository file the contracts were read from.
    contract_source: str | None = None
    #: Why declared contracts could not be read, when they could not.
    contract_problem: str | None = None

    def repository_path(self, path: str) -> str | None:
        return self.paths.get(path)

    def is_python(self, path: str) -> bool:
        return path in self.modules

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "syntax": self.syntax,
            "files": len(self.files),
            "root": self.root,
            "contracts": [c.id for c in self.contracts],
            "contract_source": self.contract_source,
            "contract_problem": self.contract_problem,
            "edits": [e.as_dict() for e in self.edits.values()],
        }


def distinctive(name: str) -> bool:
    """Whether a symbol name can be told apart from prose when a prompt
    names it: at least 4 characters, with an inner underscore
    (``tests_importing``) or an upper-case letter after a lower-case one
    (``ExplainSession``). ``run``, ``main`` and ``Calc`` are not."""
    core = name.strip("_")
    if len(core) < 4:
        return False
    if "_" in core:
        return True
    return any(a.islower() and b.isupper() for a, b in zip(core, core[1:]))


def replay_edit(text: str | None, arguments: Mapping[str, object]) -> str | None:
    """The file's text after an edit or write request, or ``None`` when it
    cannot be known.

    A write (``content``) replaces the text. An edit (``old_string`` and
    ``new_string``, or a list of them under ``edits``) replaces the first
    occurrence, or every one with ``replace_all``; an empty ``old_string``
    creates a file that has no text yet. An edit whose old text is not in
    the file (or a file whose text is unknown) yields ``None``: the tool
    refused it or the text known here is not the agent's.
    """
    content = arguments.get("content")
    if isinstance(content, str):
        return content
    raw = arguments.get("edits")
    operations: list[object] = list(raw) if isinstance(raw, list) else [arguments]
    for operation in operations:
        if not isinstance(operation, Mapping):
            return None
        old, new = operation.get("old_string"), operation.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if text is None:
            if old:
                return None
            text = ""
        if not old:
            if text:
                return None
            text = new
            continue
        if old not in text:
            return None
        every = operation.get("replace_all") is True
        text = text.replace(old, new) if every else text.replace(old, new, 1)
    return text
