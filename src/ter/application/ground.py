"""L3 use case: ground a session's analysis on its repository.

:func:`ground_session` reads the repository at the session's start commit
once, through the :class:`~ter.ports.driven.RepositoryEvidence` port
(TER-EVD-001), replays the session's edits on it, and returns a
:class:`~ter.domain.lean.grounding.RepositoryGrounding`: values only. The
analysis then stays a fold over events with no IO in the domain; the grounded
detectors read this value like any other index.

Imports are read per language: a Python import names a module, resolved
to the repository file that defines it; a TypeScript, JavaScript, Svelte or
Vue import names files (``ImportEdge.candidates``), resolved to the first
that the repository holds at the start commit or the session created.
Files under ``node_modules`` are third-party code and are not read.

Cost: one syntax tree per source file of the repository, one per edit, and
one text read per edited file; nothing per event that is not an edit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..domain.events import Event, EventId, EventKind, ToolKind
from ..domain.lean.facts import tool_paths
from ..domain.lean.grounding import (
    AddedImport,
    EditGrounding,
    RepositoryGrounding,
    distinctive,
    replay_edit,
)
from ..domain.repository import (
    ArchitectureContract,
    ImportEdge,
    RepositoryEvidenceError,
    SourceStructure,
    imported_modules,
    is_python_source,
    is_vendored,
    module_name,
    repository_path,
    resolve_import,
    session_roots,
    source_language,
)
from ..ports.driven import ArchitectureContracts, RepositoryEvidence

__all__ = ["ground_session"]

_FILE_KINDS = frozenset({ToolKind.FS_READ, ToolKind.FS_EDIT, ToolKind.FS_WRITE})
_EDIT_KINDS = frozenset({ToolKind.FS_EDIT, ToolKind.FS_WRITE})


@dataclass
class _Replayed:
    event_id: EventId
    path: str
    created: bool
    applied: bool
    structure: SourceStructure | None


def _modules_of(
    imports: Iterable[ImportEdge], modules: frozenset[str], files: frozenset[str]
) -> dict[str, int]:
    """Imported module -> first line importing it. A Python import names a
    module; an import that names files (TypeScript, JavaScript, Svelte, Vue)
    is named by the repository file it loads, and one that loads none (an
    external package) is left out."""
    out: dict[str, int] = {}
    for edge in imports:
        if edge.candidates:
            target = resolve_import(edge, files)
            if target is not None:
                out.setdefault(target, edge.line)
            continue
        for name in imported_modules(edge, modules):
            out.setdefault(name, edge.line)
    return out


def _contracts_of(
    evidence: RepositoryEvidence,
    reader: ArchitectureContracts,
    files: frozenset[str],
) -> tuple[tuple[ArchitectureContract, ...], str | None, str | None]:
    """One reader's contracts: those of the first of its sources that
    declares any (as import-linter reads one configuration file)."""
    for source in reader.sources():
        if source not in files:
            continue
        try:
            found = reader.read(source, evidence.text(source))
        except RepositoryEvidenceError as exc:
            return (), source, str(exc)
        if found:
            return found, source, None
    return (), None, None


def _contracts(
    evidence: RepositoryEvidence,
    readers: ArchitectureContracts | Sequence[ArchitectureContracts] | None,
    files: frozenset[str],
) -> tuple[tuple[ArchitectureContract, ...], str | None, str | None]:
    """Every reader's contracts, in reader order (TER-EVD-015: a Python
    package's import-linter contracts and a TypeScript package's
    dependency-cruiser rules in one repository), the files they came from,
    and the first problem met. A reader that fails leaves the others'
    contracts standing."""
    if readers is None:
        return (), None, None
    if isinstance(readers, ArchitectureContracts):
        readers = (readers,)
    contracts: list[ArchitectureContract] = []
    sources: list[str] = []
    problem: str | None = None
    for reader in readers:
        found, source, trouble = _contracts_of(evidence, reader, files)
        contracts.extend(found)
        if source is not None:
            sources.append(source)
        problem = problem or trouble
    return tuple(contracts), ", ".join(sources) or None, problem


def ground_session(
    events: Iterable[Event],
    evidence: RepositoryEvidence,
    contracts: ArchitectureContracts | Sequence[ArchitectureContracts] | None = None,
) -> RepositoryGrounding:
    """Repository evidence for one session, computed once before analysis.

    ``evidence`` must serve the repository as it was when the session
    started; ``contracts`` reads the architecture contracts it declares.
    """
    files = frozenset(evidence.files())
    requests = [
        e
        for e in events
        if e.kind is EventKind.TOOL_REQUESTED
        and e.tool is not None
        and e.tool.kind in _FILE_KINDS
    ]
    session_paths = list(
        dict.fromkeys(
            p for e in requests if e.tool for p in tool_paths(e.tool.arguments)
        )
    )
    roots = session_roots(session_paths, files)
    paths = {p: repository_path(p, roots) for p in session_paths}

    # Start commit: syntax trees of every source file (not third-party
    # code), then the import graph and symbols.
    sources = sorted(
        p for p in files if source_language(p) is not None and not is_vendored(p)
    )
    structures: dict[str, SourceStructure] = {}
    syntax = False
    for start in sources:
        structure = evidence.structure(start)
        if structure is None:
            continue
        syntax = True
        if structure.error is None:
            structures[start] = structure

    # Replay every edit in session order on the start commit's text.
    texts: dict[str, str | None] = {}
    replayed: list[_Replayed] = []
    for event in requests:
        assert event.tool is not None
        if event.tool.kind not in _EDIT_KINDS:
            continue
        named = tool_paths(event.tool.arguments)
        path = paths.get(named[0]) if named else None
        if path is None:
            continue
        created = path not in files
        before_text: str | None
        if path in texts:
            before_text = texts[path]
        elif created:
            before_text = None
        else:
            try:
                before_text = evidence.text(path)
            except RepositoryEvidenceError:
                before_text = None
        text = replay_edit(before_text, event.tool.arguments)
        texts[path] = text
        structure = (
            evidence.structure_of(path, text)
            if text is not None and source_language(path) is not None
            else None
        )
        replayed.append(_Replayed(event.id, path, created, text is not None, structure))

    created_files = {r.path for r in replayed if r.created}
    all_files = files | created_files
    modules = {
        p: module_name(p, all_files) for p in sorted(all_files) if is_python_source(p)
    }
    module_set = frozenset(modules.values())
    by_module: dict[str, list[str]] = {}
    for path, module in modules.items():
        by_module.setdefault(module, []).append(path)

    def resolve(names: Iterable[str]) -> tuple[str, ...]:
        """Imported modules -> repository files (a file-named import is its
        own file)."""
        return tuple(
            sorted(
                {
                    p
                    for n in names
                    for p in by_module.get(n, (n,) if n in all_files else ())
                }
            )
        )

    def imported(s: SourceStructure) -> dict[str, int]:
        return _modules_of(s.imports, module_set, all_files)

    links = {p: resolve(imported(s)) for p, s in structures.items()}
    importers: dict[str, list[str]] = {}
    for importer, targets in links.items():
        for target in targets:
            importers.setdefault(target, []).append(importer)
    symbols: dict[str, set[str]] = {}
    for path, s in structures.items():
        for symbol in s.symbols:
            leaf = symbol.name.rsplit(".", 1)[-1]
            if distinctive(leaf):
                symbols.setdefault(leaf.lower(), set()).add(path)

    # Each edit's imports after it, and what it added to the file's imports
    # just before it (unknown when the file's text or syntax tree was).
    state: dict[str, frozenset[str] | None] = {
        p: frozenset(imported(s)) for p, s in structures.items()
    }
    edits: dict[EventId, EditGrounding] = {}
    for r in replayed:
        before: frozenset[str] | None
        if r.path in state:
            before = state[r.path]
        elif r.created:
            before = frozenset()
        else:
            before = None  # an existing file whose imports were never read
        parsed = r.structure is not None and r.structure.error is None
        after = imported(r.structure) if r.structure is not None and parsed else None
        added: tuple[AddedImport, ...] = ()
        if after is not None and before is not None:
            added = tuple(
                sorted(
                    AddedImport(m, line) for m, line in after.items() if m not in before
                )
            )
        state[r.path] = frozenset(after) if after is not None else None
        edits[r.event_id] = EditGrounding(
            event_id=r.event_id,
            path=r.path,
            module=modules.get(r.path),
            created=r.created,
            applied=r.applied,
            parsed=parsed,
            imports=tuple(sorted(after)) if after is not None else (),
            links=resolve(after) if after is not None else (),
            added=added,
        )

    found, source, problem = _contracts(evidence, contracts, files)
    return RepositoryGrounding(
        engine=evidence.name,
        syntax=syntax,
        files=files,
        modules=modules,
        links=links,
        importers={k: tuple(sorted(v)) for k, v in importers.items()},
        symbols={k: tuple(sorted(v)) for k, v in sorted(symbols.items())},
        root=roots[0] if roots else None,
        roots=roots,
        paths=paths,
        edits=edits,
        contracts=found,
        contract_source=source,
        contract_problem=problem,
    )
