"""Context bundles (L3): the evidence selected for the agent's next decision.

A :class:`ContextBundle` is what TER would hand the agent before it acts on a
prompt: the repository files the prompt's change surface needs, each one a
fragment with an id, its source, the reason it was selected and its token
count, inside a token budget (TER-CTX-001, points 151, 152 and 159). Every
fragment TER supplies is recorded as one ``context.supplied`` event
(TER-EVD-004, point 160), so what the bundle held can later be compared with
what the session used (:mod:`ter.domain.context_metrics`).

**Selection** is structural and comes from the expected change surface
(:mod:`ter.domain.lean.surface`), never from token counts or word scores:

1. *seeds*: the repository files the prompt names (file name, dotted module
   name, distinctive symbol); a prompt that names none inherits the seeds the
   caller passes (the last prompt that named some), else selects nothing;
2. *tests of a seed*: test modules that import a seed or that a seed imports;
3. *neighbours*: files a seed imports or is imported by;
4. *tests of a neighbour*.

Within a rank, files come in path order. Third-party code (``node_modules``,
minified bundles) is never selected.

**Packing** walks the selection in that order. A seed or a test of a seed
whose whole text fits the remaining budget is a ``file`` fragment, else its
outline (imports and definitions, read from its syntax) when that fits. A
neighbour or a test of a neighbour is supplied in its smaller form, usually
the outline, which says what the file offers.
What fits in no form is an *omission*, listed with its reason. Nothing of higher rank is ever dropped to
make room for something of lower rank, and a bundle that could not hold every
seed, or that has no seed at all, says it is ``insufficient`` instead of
passing for complete: the aim is sufficient evidence, not the smallest
bundle (P159).

**Determinism.** A bundle is a pure function of the prompt text, the budget,
the repository content and the tokenizer: ids are content hashes, every
collection is sorted, and :meth:`ContextBundle.to_json` and
:meth:`ContextBundle.render` are canonical, so the same inputs give
byte-identical bundles wherever the repository lives (P151).

The domain does no IO: the application reads texts and syntax through the
``RepositoryEvidence`` port and passes them in as :class:`Candidate` values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .events import Actor, Event, EventId, EventKind, Provenance, make_event_id
from .lean.grounding import RepositoryGrounding
from .lean.surface import files_named, surface_of
from .repository import SourceStructure, is_vendored

__all__ = [
    "BUNDLE_SCHEMA",
    "CONTEXT_SOURCE",
    "Candidate",
    "ContextBundle",
    "ContextFragment",
    "FragmentForm",
    "FragmentRole",
    "Omission",
    "Selection",
    "SuppliedFragment",
    "assemble_bundle",
    "outline_of",
    "read_supplied",
    "select_evidence",
    "supplied_events",
]

#: Schema of a bundle written as JSON.
BUNDLE_SCHEMA = "ter.context-bundle/1"
#: Provenance source of every ``context.supplied`` event.
CONTEXT_SOURCE = "ter.context"


class FragmentRole(StrEnum):
    """Why a file is in the change surface, in selection order."""

    SEED = "seed"
    SEED_TEST = "seed_test"
    NEIGHBOUR = "neighbour"
    NEIGHBOUR_TEST = "neighbour_test"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def whole(self) -> bool:
        """Seeds and their tests are supplied whole when they fit; the rest
        of the surface as outlines, which say what a file offers."""
        return self in (FragmentRole.SEED, FragmentRole.SEED_TEST)


_RANK = {role: i for i, role in enumerate(FragmentRole)}


class FragmentForm(StrEnum):
    """How much of a file a fragment holds."""

    FILE = "file"
    OUTLINE = "outline"


@dataclass(frozen=True)
class Selection:
    """A repository file selected for the next decision, and why."""

    path: str
    role: FragmentRole
    reason: str


@dataclass(frozen=True)
class Candidate:
    """A selected file with what the repository says about it: its text
    (``None`` when it cannot be read) and its outline (``None`` when the
    engine reads no syntax for it)."""

    selection: Selection
    text: str | None
    outline: str | None = None


@dataclass(frozen=True)
class ContextFragment:
    """One piece of evidence in a bundle."""

    id: str
    source: str
    role: FragmentRole
    form: FragmentForm
    reason: str
    tokens: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source": self.source,
            "role": self.role.value,
            "form": self.form.value,
            "reason": self.reason,
            "tokens": self.tokens,
            "text": self.text,
        }


@dataclass(frozen=True)
class Omission:
    """A selected file the budget could not hold, even as an outline."""

    source: str
    role: FragmentRole
    reason: str
    #: Tokens of the smallest form that was available (0: none was).
    tokens: int
    why: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "role": self.role.value,
            "reason": self.reason,
            "tokens": self.tokens,
            "why": self.why,
        }


@dataclass(frozen=True)
class ContextBundle:
    """The evidence selected for one decision, within a token budget."""

    id: str
    session_id: str
    #: The prompt event the bundle answers, when it is an event of the session.
    prompt: EventId | None
    #: SHA-256 prefix of the prompt text (the text itself is not kept).
    prompt_digest: str
    budget: int
    #: ``named`` (the prompt names the seeds), ``inherited`` or ``none``.
    basis: str
    seeds: tuple[str, ...]
    fragments: tuple[ContextFragment, ...]
    omitted: tuple[Omission, ...]
    #: Tokenizer and repository engine the bundle was built with.
    tokenizer: str
    engine: str

    @property
    def tokens(self) -> int:
        return sum(f.tokens for f in self.fragments)

    @property
    def sources(self) -> frozenset[str]:
        return frozenset(f.source for f in self.fragments)

    @property
    def insufficient(self) -> bool:
        """True when the bundle lacks a seed, or has none to hold."""
        return not self.seeds or any(o.role is FragmentRole.SEED for o in self.omitted)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": BUNDLE_SCHEMA,
            "id": self.id,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "prompt_digest": self.prompt_digest,
            "budget": self.budget,
            "tokens": self.tokens,
            "basis": self.basis,
            "seeds": list(self.seeds),
            "insufficient": self.insufficient,
            "tokenizer": self.tokenizer,
            "engine": self.engine,
            "fragments": [f.as_dict() for f in self.fragments],
            "omitted": [o.as_dict() for o in self.omitted],
        }

    def to_json(self) -> str:
        """Canonical JSON: same bundle, same bytes."""
        return (
            json.dumps(self.as_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )

    def render(self) -> str:
        """The bundle as Markdown, for handing to the agent."""
        lines = [
            f"# Context bundle {self.id}",
            "",
            f"{len(self.fragments)} fragment(s), {self.tokens} of {self.budget} "
            f"tokens; seeds {self.basis}: {', '.join(self.seeds) or 'none'}.",
        ]
        if self.insufficient:
            lines.append(
                "Insufficient: "
                + (
                    "the prompt names no repository file."
                    if not self.seeds
                    else "a seed did not fit the budget."
                )
            )
        for f in self.fragments:
            lines += [
                "",
                f"## {f.source} ({f.form.value}, {f.tokens} tokens)",
                "",
                f"`{f.id}` · {f.role.value}: {f.reason}",
                "",
                "~~~~",
                f.text.rstrip("\n"),
                "~~~~",
            ]
        if self.omitted:
            lines += ["", "## Omitted", ""]
            lines += [
                f"- {o.source} ({o.role.value}, {o.tokens} tokens): {o.why}"
                for o in self.omitted
            ]
        return "\n".join(lines) + "\n"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _first(paths: Iterable[str], among: Iterable[str]) -> str | None:
    wanted = set(among)
    return next((p for p in sorted(paths) if p in wanted), None)


def select_evidence(
    g: RepositoryGrounding,
    words: frozenset[str],
    inherited: Sequence[str] = (),
) -> tuple[str, tuple[str, ...], tuple[Selection, ...]]:
    """The basis, the seeds and the ordered selection for a prompt's words.

    Selection follows the change surface rules (TER-EVD-006) at the start
    commit; ``inherited`` are the seeds of the last prompt that named some,
    used when this one names none.
    """
    named = tuple(p for p in files_named(g, words) if not is_vendored(p))
    if named:
        basis, seeds = "named", named
    elif inherited:
        basis, seeds = "inherited", tuple(sorted(set(inherited)))
    else:
        return "none", (), ()
    neighbours, tests = surface_of(g, seeds)
    seed_set = set(seeds)

    def links(path: str) -> set[str]:
        return set(g.links.get(path, ())) | set(g.importers.get(path, ()))

    out: list[Selection] = [
        Selection(
            s,
            FragmentRole.SEED,
            "named by the prompt"
            if basis == "named"
            else "named by an earlier prompt this one continues",
        )
        for s in seeds
    ]
    seed_tests: list[Selection] = []
    other_tests: list[Selection] = []
    for t in tests:
        if is_vendored(t):
            continue
        via = _first(links(t), seed_set)
        if via is not None:
            seed_tests.append(
                Selection(t, FragmentRole.SEED_TEST, f"test linked to seed {via}")
            )
            continue
        near = _first(links(t), neighbours) or "a neighbour"
        other_tests.append(
            Selection(t, FragmentRole.NEIGHBOUR_TEST, f"test linked to {near}")
        )
    out += seed_tests
    for n in neighbours:
        if is_vendored(n):
            continue
        seed = _first(g.links.get(n, ()), seed_set)
        if seed is not None:
            reason = f"imports seed {seed}"
        else:
            reason = f"imported by seed {_first(g.importers.get(n, ()), seed_set)}"
        out.append(Selection(n, FragmentRole.NEIGHBOUR, reason))
    out += other_tests
    return basis, seeds, tuple(out)


def outline_of(structure: SourceStructure) -> str | None:
    """A file's imports and definitions, one per line (``None`` when the file
    did not parse): the smaller form of a fragment."""
    if structure.error is not None:
        return None
    lines = [f"# outline of {structure.path} ({structure.language})"]
    lines += [
        f"import {e.module}"
        + (f" ({', '.join(e.names)})" if e.names else "")
        + f"  # line {e.line}"
        for e in structure.imports
    ]
    lines += [
        f"{s.kind.value} {s.name}  # lines {s.line}-{s.end_line}"
        for s in structure.symbols
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def _fragment(
    c: Candidate, form: FragmentForm, text: str, tokens: int
) -> ContextFragment:
    s = c.selection
    return ContextFragment(
        id="ctx-" + _digest(s.path, form.value, text),
        source=s.path,
        role=s.role,
        form=form,
        reason=s.reason,
        tokens=tokens,
        text=text,
    )


def assemble_bundle(
    *,
    session_id: str,
    prompt_text: str,
    candidates: Sequence[Candidate],
    budget: int,
    count: Callable[[str], int],
    basis: str,
    seeds: Sequence[str],
    prompt: EventId | None = None,
    tokenizer: str = "",
    engine: str = "",
) -> ContextBundle:
    """Pack the selected candidates, in order, into ``budget`` tokens."""
    if budget < 0:
        raise ValueError(f"budget must be 0 or more, got {budget}")
    left = budget
    fragments: list[ContextFragment] = []
    omitted: list[Omission] = []
    for c in sorted(candidates, key=lambda c: c.selection.role.rank):
        s = c.selection
        full = count(c.text) if c.text is not None else None
        short = count(c.outline) if c.outline is not None else None
        forms = [
            (form, text, n)
            for form, text, n in (
                (FragmentForm.FILE, c.text, full),
                (FragmentForm.OUTLINE, c.outline, short),
            )
            if text is not None and n is not None
        ]
        if not s.role.whole:
            # The smaller form: what the file offers, at the least cost.
            forms = sorted(forms, key=lambda f: f[2])[:1]
        fitted = next(((f, t, n) for f, t, n in forms if n <= left), None)
        if fitted is not None:
            form, text, n = fitted
            fragments.append(_fragment(c, form, text, n))
            left -= n
            continue
        if not forms:
            omitted.append(
                Omission(
                    s.path, s.role, s.reason, 0, "the repository could not supply it"
                )
            )
            continue
        smallest = min(n for _, _, n in forms)
        why = f"its smallest form needs {smallest} tokens and {left} are left"
        omitted.append(Omission(s.path, s.role, s.reason, smallest, why))
    digest = _digest(prompt_text)
    bundle_id = "bnd-" + _digest(
        session_id, digest, str(budget), *(f.id for f in fragments)
    )
    return ContextBundle(
        id=bundle_id,
        session_id=session_id,
        prompt=prompt,
        prompt_digest=digest,
        budget=budget,
        basis=basis,
        seeds=tuple(seeds),
        fragments=tuple(fragments),
        omitted=tuple(omitted),
        tokenizer=tokenizer,
        engine=engine,
    )


# ---------------------------------------------------------------------------
# context.supplied events (TER-EVD-004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuppliedFragment:
    """What one ``context.supplied`` event says was handed to the agent."""

    event_id: EventId
    bundle: str
    fragment: str
    source: str
    reason: str
    role: str
    form: str
    tokens: int


def supplied_events(
    bundle: ContextBundle,
    *,
    start_sequence: int = 0,
    timestamp: datetime | None = None,
) -> tuple[Event, ...]:
    """One ``context.supplied`` event per fragment, in bundle order.

    The text is canonical JSON naming the bundle, the fragment id, its source,
    the reason it was selected, its role, form and tokens; the fragment's text
    is not repeated. Ids derive from the session, bundle and fragment, so
    supplying the same bundle twice yields the same events (the analysis
    reads a redelivery once).
    """
    events: list[Event] = []
    for i, f in enumerate(bundle.fragments):
        body = {
            "bundle": bundle.id,
            "fragment": f.id,
            "source": f.source,
            "reason": f.reason,
            "role": f.role.value,
            "form": f.form.value,
            "tokens": f.tokens,
        }
        events.append(
            Event(
                id=make_event_id(CONTEXT_SOURCE, bundle.session_id, bundle.id, f.id),
                session_id=bundle.session_id,
                sequence=start_sequence + i,
                kind=EventKind.CONTEXT_SUPPLIED,
                actor=Actor.SYSTEM,
                text=json.dumps(body, sort_keys=True, ensure_ascii=False),
                provenance=Provenance(
                    source=CONTEXT_SOURCE,
                    record_id=bundle.id,
                    block_index=i,
                    fingerprint=f.id,
                ),
                timestamp=timestamp,
                parent_id=bundle.prompt,
            )
        )
    return tuple(events)


def read_supplied(event: Event) -> SuppliedFragment | None:
    """The fragment a ``context.supplied`` event records (``None`` for any
    other event, or one whose text is not a fragment record)."""
    if event.kind is not EventKind.CONTEXT_SUPPLIED:
        return None
    try:
        body = json.loads(event.text)
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    try:
        return SuppliedFragment(
            event_id=event.id,
            bundle=str(body["bundle"]),
            fragment=str(body["fragment"]),
            source=str(body["source"]),
            reason=str(body["reason"]),
            role=str(body.get("role", "")),
            form=str(body.get("form", "")),
            tokens=int(body.get("tokens", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None
