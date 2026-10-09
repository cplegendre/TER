"""Evidence usage (L3, TER-EVD-008): did anything later use what a read returned?

For every **repository read** the session's later events are searched for a
structural use of that file. A repository read is an ``fs.read`` request
whose path is a file of the session's repository, or a shell command read as
exploration (``cat``, ``head``, ``grep``: ``ShellIntent.EXPLORE``) or a
``sed -n`` print
that names repository files by path (relative to the root, or absolute under
a session root); a command naming several files is one read of each.
*Used* is defined from structure only, never from
word-overlap scores or token counts. Each rule below names the use kind it
records, and whether it is **material** (a change, a command or a check acted
on the file's content) or only informed a decision:

``edited`` (material)
    A later edit or write changes the same repository file.
``imported_by_edit`` (material)
    A later edit changes a file that imports the read file, at the start
    commit or after that edit (the read file is a dependency of the change).
``named_in_edit`` (material)
    A later edit's text (its old and new text or written content, not its own
    path) names the read file: its base name, its dotted module name, or a
    distinctive symbol it defines (``price_with_tax``, ``ExplainSession``).
``named_in_command`` (material)
    A later shell command that neither checks nor explores names the read file
    the same way (``python scripts/tool.py``, ``python -m pkg.core``); reading
    the file again is not a use of the first read.
``tested`` (material)
    A later check (a validation command, or a change line that runs a check
    tool) names the read file or a test module that imports it.
``named_in_decision`` (not material)
    A later reasoning block or response names the read file the same way.

A name counts only when it names **one** file: a base name, a stem or a
symbol that more than one repository file shares (``__init__.py``,
``index.ts``, a ``run`` defined twice) names none of them. Stems count only
when they are distinctive (``fragment_store``); module names always contain
a dot and are unique.

A read with at least one use is ``used``; a read nothing used is ``unused``
once a response follows it (the agent has acted on what it knew), and
``pending`` before that, so a live session is not judged early. Only the
first use of each kind is recorded, with the event id that shows it.

Reads of paths outside the repository (``/tmp``, harness state) are not
repository reads and are not judged. Searches and fetches are judged by the
outcome value (:mod:`.value`), not here.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath

from ..events import EventId, EventKind, ToolKind
from ..repository import is_test_module, repository_path
from .facts import content_words
from .grounding import RepositoryGrounding, distinctive
from .model import ShellIntent, Step

__all__ = [
    "USAGE_RULES",
    "EvidenceUsage",
    "FileNames",
    "FileRole",
    "ReadUsage",
    "Use",
    "UseKind",
    "UsageStatus",
    "evidence_usage",
    "file_role",
    "read_targets",
]


class UseKind(StrEnum):
    EDITED = "edited"
    IMPORTED_BY_EDIT = "imported_by_edit"
    NAMED_IN_EDIT = "named_in_edit"
    NAMED_IN_COMMAND = "named_in_command"
    TESTED = "tested"
    NAMED_IN_DECISION = "named_in_decision"

    @property
    def material(self) -> bool:
        """A change, a command or a check acted on the content."""
        return self is not UseKind.NAMED_IN_DECISION


#: The published rule of each use kind (``docs/ter4/l3-grounded.md``).
USAGE_RULES: Mapping[UseKind, str] = {
    UseKind.EDITED: "a later edit or write changes the same repository file",
    UseKind.IMPORTED_BY_EDIT: (
        "a later edit changes a file that imports the read file, at the start "
        "commit or after that edit"
    ),
    UseKind.NAMED_IN_EDIT: (
        "a later edit's text (not its own path) names the read file by its "
        "unique base name, distinctive stem, dotted module name or a "
        "distinctive symbol only it defines"
    ),
    UseKind.NAMED_IN_COMMAND: (
        "a later shell command that is not a check names the read file the same way"
    ),
    UseKind.TESTED: (
        "a later check names the read file or a test module that imports it"
    ),
    UseKind.NAMED_IN_DECISION: (
        "a later reasoning block or response names the read file the same way "
        "(informs a decision; not material)"
    ),
}


class UsageStatus(StrEnum):
    USED = "used"
    UNUSED = "unused"
    #: No response follows the read yet: not judged.
    PENDING = "pending"


class FileRole(StrEnum):
    """What a repository file is for, read from its path alone."""

    SOURCE = "source"
    TEST = "test"
    DOC = "doc"
    CI = "ci"
    CONFIG = "config"


_DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_CONFIG_SUFFIXES = frozenset(
    {".toml", ".yaml", ".yml", ".ini", ".cfg", ".json", ".jsonc", ".lock", ".env"}
)
_CONFIG_NAMES = frozenset(
    {
        "makefile",
        "dockerfile",
        "justfile",
        "procfile",
        "gemfile",
        "pipfile",
        "setup.py",
        "conftest.py",
        "noxfile.py",
        "tox.ini",
        "license",
        "licence",
        "copying",
    }
)
_CI_FILES = frozenset(
    {".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml", ".travis.yml"}
)
_CI_DIRS = frozenset({".github", ".circleci", ".gitlab", ".buildkite"})
_DOC_DIRS = frozenset({"docs", "doc", "documentation"})
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "testing"})


def file_role(path: str) -> FileRole:
    """The role of a repository file by its path: a test (``test_*.py``, a
    ``*.test.ts``, anything under a ``tests`` directory), CI (under
    ``.github``, ``.circleci``; ``.gitlab-ci.yml``, ``Jenkinsfile``), a doc
    (``.md``, ``.rst``, ``.txt``, ``.adoc``, or under ``docs``), config (data
    and build files: ``.toml``, ``.yaml``, ``.json``, ``.ini``, ``.cfg``,
    dotfiles, ``Makefile``, ``Dockerfile``, ``setup.py``, ``conftest.py``, a
    licence), else source."""
    parts = PurePosixPath(path).parts
    name = parts[-1].lower() if parts else ""
    dirs = {p.lower() for p in parts[:-1]}
    if dirs & _CI_DIRS or name in _CI_FILES:
        return FileRole.CI
    if is_test_module(path) or dirs & _TEST_DIRS:
        return FileRole.TEST
    suffix = PurePosixPath(name).suffix
    if suffix in _DOC_SUFFIXES or dirs & _DOC_DIRS:
        return FileRole.DOC
    if suffix in _CONFIG_SUFFIXES or name in _CONFIG_NAMES or name.startswith("."):
        return FileRole.CONFIG
    return FileRole.SOURCE


class FileNames:
    """The words that name exactly one repository file.

    From the repository's files and the files the session created: the base
    name (``pricing.py``), a distinctive stem (``fragment_store``), the dotted
    module name (``app.domain.pricing``) and the distinctive symbols a file
    defines, each kept only when no other file shares it.
    """

    def __init__(self, g: RepositoryGrounding) -> None:
        files = sorted(g.files | {e.path for e in g.edits.values()})
        candidates: dict[str, set[str]] = {}

        def add(word: str, path: str) -> None:
            candidates.setdefault(word, set()).add(path)

        for path in files:
            base = PurePosixPath(path).name.lower()
            add(base, path)
            stem = base.rsplit(".", 1)[0]
            if stem != base and distinctive(stem):
                add(stem, path)
            module = g.modules.get(path)
            if module and "." in module:
                add(module.lower(), path)
        for symbol, paths in g.symbols.items():
            for path in paths:
                add(symbol.lower(), path)
        self._defined = {s.lower(): set(p) for s, p in g.symbols.items()}
        self.path_of: dict[str, str] = {
            word: next(iter(paths))
            for word, paths in candidates.items()
            if len(paths) == 1
        }
        self.names_of: dict[str, set[str]] = {}
        for word, path in self.path_of.items():
            self.names_of.setdefault(path, set()).add(word)

    def names(self, path: str, identifiers: Iterable[str] = ()) -> frozenset[str]:
        """The words naming ``path``, plus distinctive ``identifiers`` its read
        text defines that no other repository file defines."""
        extra = {
            low
            for i in identifiers
            if distinctive(i)
            and (low := i.lower()) not in self.path_of
            and self._defined.get(low, {path}) <= {path}
        }
        return frozenset(self.names_of.get(path, set()) | extra)

    def named(self, words: Iterable[str]) -> frozenset[str]:
        """The repository files ``words`` name."""
        return frozenset(p for w in words if (p := self.path_of.get(w)) is not None)


@dataclass(frozen=True)
class Use:
    kind: UseKind
    event_id: EventId

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "event_id": self.event_id,
            "material": self.kind.material,
        }


@dataclass(frozen=True)
class ReadUsage:
    """One repository read and what later used it."""

    event_id: EventId
    result: EventId | None
    path: str
    #: ``read`` for a file read tool, ``shell`` for an exploring command.
    via: str
    status: UsageStatus
    uses: tuple[Use, ...]
    #: Tokens of the read's output carried in the context.
    context_tokens: int

    @property
    def material(self) -> bool:
        """A later change, command or check used it (point 69)."""
        return any(u.kind.material for u in self.uses)

    @property
    def evidence(self) -> tuple[EventId, ...]:
        """The read, its result and every use, in that order."""
        own = (self.event_id,) + ((self.result,) if self.result else ())
        return own + tuple(u.event_id for u in self.uses)

    def first(self, *kinds: UseKind) -> Use | None:
        return next((u for u in self.uses if u.kind in kinds), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "result": self.result,
            "path": self.path,
            "via": self.via,
            "status": self.status.value,
            "material": self.material,
            "uses": [u.as_dict() for u in self.uses],
            "context_tokens": self.context_tokens,
        }


@dataclass(frozen=True)
class EvidenceUsage:
    """Evidence usage of every repository read of a session (TER-EVD-008)."""

    reads: tuple[ReadUsage, ...] = ()
    #: Repository files the session read, in first-read order.
    explored: tuple[str, ...] = ()
    #: Repository files the session edited or wrote, in first-edit order.
    changed: tuple[str, ...] = ()

    def of(self, event_id: EventId) -> tuple[ReadUsage, ...]:
        """The reads one request made (a shell command may read several)."""
        return tuple(r for r in self.reads if r.event_id == event_id)

    def count(self, status: UsageStatus) -> int:
        return sum(r.status is status for r in self.reads)

    @property
    def share_used(self) -> float | None:
        """Used reads over judged reads (point 68); ``None`` with none judged."""
        judged = self.count(UsageStatus.USED) + self.count(UsageStatus.UNUSED)
        return self.count(UsageStatus.USED) / judged if judged else None

    @property
    def unused_tokens(self) -> int:
        """Context tokens of unused reads (point 70)."""
        return sum(
            r.context_tokens for r in self.reads if r.status is UsageStatus.UNUSED
        )

    def as_dict(self) -> dict[str, object]:
        changed = set(self.changed)
        share = self.share_used
        return {
            "rules": {k.value: v for k, v in USAGE_RULES.items()},
            "summary": {
                "reads": len(self.reads),
                "used": self.count(UsageStatus.USED),
                "material": sum(r.material for r in self.reads),
                "unused": self.count(UsageStatus.UNUSED),
                "pending": self.count(UsageStatus.PENDING),
                "share_used": None if share is None else round(share, 4),
                "unused_context_tokens": self.unused_tokens,
                "files_explored": len(self.explored),
                "files_changed": len(self.changed),
                "explored_and_changed": sum(p in changed for p in self.explored),
            },
            "explored": list(self.explored),
            "changed": list(self.changed),
            "reads": [r.as_dict() for r in self.reads],
        }


_TOKEN = re.compile(r"[^\s'\";|&<>()=]+")
#: ``sed -n`` prints and changes nothing, but the L2 shell reading does not
#: list it as exploration; here it is a read like ``cat``.
_SED_PRINT = re.compile(r"^\s*sed\s+-n\b(?![^;&|]*\s-i)")


def _reads_files(step: Step) -> bool:
    """A shell request that only reads: exploration, or a ``sed -n`` print."""
    if step.tool_kind is not ToolKind.EXEC_SHELL or not step.command:
        return False
    return step.shell is ShellIntent.EXPLORE or (
        step.shell is ShellIntent.OTHER and _SED_PRINT.match(step.command) is not None
    )


def read_targets(step: Step, g: RepositoryGrounding) -> tuple[tuple[str, ...], str]:
    """The repository files a request reads, and how (``read`` or ``shell``)."""
    if not step.is_request:
        return (), ""
    if step.tool_kind is ToolKind.FS_READ and step.paths:
        path = g.repository_path(step.paths[0])
        return ((path,), "read") if path is not None else ((), "")
    if not _reads_files(step) or not step.command:
        return (), ""
    known = g.files | {e.path for e in g.edits.values()}
    found: dict[str, None] = {}
    for token in _TOKEN.findall(step.command):
        path = repository_path(token, g.roots or g.root)
        if path is not None and path in known:
            found.setdefault(path)
    return tuple(found), "shell"


def _path_words(step: Step) -> frozenset[str]:
    return frozenset(w for p in step.paths for w in content_words(p))


@dataclass(frozen=True)
class _Later:
    """Later steps that may use a read, indexed by word and kind."""

    indexes: dict[tuple[UseKind, str], list[int]]
    by_index: dict[int, Step]


def _index(steps: Sequence[Step]) -> _Later:
    indexes: dict[tuple[UseKind, str], list[int]] = {}
    for step in steps:
        kind: UseKind | None = None
        words: frozenset[str] = frozenset()
        if step.is_edit:
            kind, words = UseKind.NAMED_IN_EDIT, step.words - _path_words(step)
        elif step.is_request and step.checks:
            kind, words = UseKind.TESTED, step.words
        elif (
            step.is_request
            and step.tool_kind is ToolKind.EXEC_SHELL
            and not _reads_files(step)
        ):
            kind, words = UseKind.NAMED_IN_COMMAND, step.words
        elif step.kind in (EventKind.REASONING, EventKind.RESPONSE):
            kind, words = UseKind.NAMED_IN_DECISION, step.words
        if kind is None:
            continue
        for word in words:
            indexes.setdefault((kind, word), []).append(step.index)
    return _Later(indexes, {s.index: s for s in steps})


def _first_after(
    later: _Later, kind: UseKind, words: Iterable[str], after: int
) -> int | None:
    best: int | None = None
    for word in words:
        found = later.indexes.get((kind, word))
        if not found:
            continue
        at = bisect_right(found, after)
        if at < len(found) and (best is None or found[at] < best):
            best = found[at]
    return best


def evidence_usage(
    steps: Sequence[Step],
    g: RepositoryGrounding,
    completion_of: Mapping[int, Step],
    names: FileNames | None = None,
) -> EvidenceUsage:
    """Judge every repository read of ``steps`` (TER-EVD-008).

    Cost: one pass to index later steps by word, then per read one lookup per
    name it has and one pass over the session's edits.
    """
    names = names if names is not None else FileNames(g)
    later = _index(steps)
    edits: list[tuple[int, Step, str]] = [
        (s.index, s, p)
        for s in steps
        if s.is_edit and s.paths and (p := g.repository_path(s.paths[0])) is not None
    ]
    responses = [s.index for s in steps if s.kind is EventKind.RESPONSE]
    explored: dict[str, None] = {}
    reads: list[ReadUsage] = []
    for step in steps:
        targets, via = read_targets(step, g)
        for path in targets:
            explored.setdefault(path)
            reads.append(
                _judge(
                    step, path, via, g, names, later, edits, responses, completion_of
                )
            )
            if len(targets) > 1:
                # One command read several files: its output is shared.
                last = reads[-1]
                reads[-1] = replace(
                    last, context_tokens=last.context_tokens // len(targets)
                )
    changed = tuple(dict.fromkeys(p for _, _, p in edits))
    return EvidenceUsage(tuple(reads), tuple(explored), changed)


def _judge(
    step: Step,
    path: str,
    via: str,
    g: RepositoryGrounding,
    names: FileNames,
    later: _Later,
    edits: Sequence[tuple[int, Step, str]],
    responses: Sequence[int],
    completion_of: Mapping[int, Step],
) -> ReadUsage:
    result = completion_of.get(step.index)
    after = result.index if result is not None else step.index
    identifiers = result.identifiers if result is not None else frozenset()
    own = names.names(path, identifiers)
    tests = {t for t in g.importers.get(path, ()) if is_test_module(t)} - {path}
    tested = own | frozenset(w for t in sorted(tests) for w in names.names(t))
    found: dict[UseKind, int] = {}
    for index, edit, edited in edits:
        if index <= after:
            continue
        if edited == path:
            found.setdefault(UseKind.EDITED, index)
        grounded = g.edits.get(edit.event_id)
        if path in g.links.get(edited, ()) or (
            grounded is not None and path in grounded.links
        ):
            found.setdefault(UseKind.IMPORTED_BY_EDIT, index)
    for kind, words in (
        (UseKind.NAMED_IN_EDIT, own),
        (UseKind.NAMED_IN_COMMAND, own),
        (UseKind.TESTED, tested),
        (UseKind.NAMED_IN_DECISION, own),
    ):
        hit = _first_after(later, kind, words, after)
        if hit is not None:
            found[kind] = hit
    uses = tuple(
        Use(kind, later.by_index[i].event_id)
        for kind, i in sorted(found.items(), key=lambda p: (p[1], p[0].value))
    )
    if uses:
        status = UsageStatus.USED
    elif responses and responses[-1] > after:
        status = UsageStatus.UNUSED
    else:
        status = UsageStatus.PENDING
    return ReadUsage(
        event_id=step.event_id,
        result=result.event_id if result is not None else None,
        path=path,
        via=via,
        status=status,
        uses=uses,
        context_tokens=result.context_tokens if result is not None else 0,
    )
