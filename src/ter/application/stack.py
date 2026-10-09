"""Session stack from repository manifests (L3, TER-STK-002).

:func:`read_stack` reads a repository's manifests through the
:class:`~ter.ports.driven.RepositoryEvidence` port (TER-EVD-001) and parses
them with the pure rules of :mod:`ter.domain.stack`.
"""

from __future__ import annotations

from ..domain.repository import RepositoryEvidenceError
from ..domain.stack import (
    RepositoryStack,
    StackFact,
    is_manifest,
    manifest_facts,
    merge_facts,
)
from ..ports.driven import RepositoryEvidence

__all__ = ["MANIFEST_LIMIT", "read_stack"]

#: Manifests read per repository at most (shallowest first); more are counted
#: as truncated, so a huge monorepo costs a bounded number of reads.
MANIFEST_LIMIT = 200


def read_stack(
    evidence: RepositoryEvidence, limit: int = MANIFEST_LIMIT
) -> RepositoryStack:
    """The stack the repository's manifests declare, each fact citing them."""
    listed = sorted(
        (p for p in evidence.files() if is_manifest(p)),
        key=lambda p: (p.count("/"), p),
    )
    chosen = listed[:limit]
    facts: list[StackFact] = []
    read: list[str] = []
    unreadable: list[str] = []
    for path in chosen:
        try:
            text = evidence.text(path)
        except RepositoryEvidenceError:
            # A binary lock file (bun.lockb) still names its package manager.
            unreadable.append(path)
            text = ""
        read.append(path)
        facts.extend(manifest_facts(path, text))
    return RepositoryStack(
        facts=merge_facts(facts),
        manifests=tuple(sorted(read)),
        unreadable=tuple(sorted(unreadable)),
        truncated=len(listed) > len(chosen),
    )
