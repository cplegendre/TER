"""Context precision, recall, carrying cost and defect risk (L3).

Read from a session's events after the fact, beside the analysis fold: the
``context.supplied`` events say what each bundle held (TER-EVD-004), and the
events after them say what the session went on to do. Linear in the session.

**A bundle's window** runs from its first ``context.supplied`` event to the
next bundle's first one, the next task's prompt (the first prompt after a
tool request in the window: a prompt that arrives before any tool request is
the one the bundle was built ahead of), or the end of the session.

**Used** (TER-CTX-003, point 154). A supplied fragment is used when, in its
bundle's window, an edit or write changes its file, or an edit's new text or
a shell command names one of its file's distinctive symbols, or a shell
command names its path or file name (running its tests, linting it). Reading
the file again is not use. This is a small local rule; the general evidence
usage model (``ter.domain.lean.usage``, TER-EVD-008) is built separately and
the two should converge on one rule.

**Precision** is the share of a bundle's fragments that were used.

**Needed** items are the repository files (present at the start commit) the
session went on to edit in the window, and the test modules a shell command
in the window names. With a critical evidence list for the session, the
needed items are instead the listed items whose first dependent edit falls
in the window. **Recall** is the share of needed items the bundle held
(point 155). Either is ``None`` when its denominator is empty.

**Critical recall** (TER-EVD-005): for every listed item, the first
*dependent* edit is the first edit that changes the item's file, changes a
file that imports it (at the start commit or after the edit), or whose new
text names the item's symbol. The item was *in context* when a bundle
supplied its file, or the agent read its file, before that edit. Recall is
the share of items with a dependent edit that were in context; items no edit
depended on are listed apart and left out of the share.

**Inventory and defect risk** (TER-CTX-004, points 156 to 158). Unused
fragments are inventory: their tokens are carried through every later model
turn and priced like the context inventory of :mod:`ter.domain.costing`: on
the first model turn after the supply at the cache-write rate when that turn
shows caching (else the input rate), then on every later turn at the
cache-read rate (else input), with the rates of each turn's model from the
price book. Needed items the bundle lacked are missing context, a potential
defect source: each lists the edits (or test runs) that depended on it and
whether the agent fetched it itself by reading it later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath

from .costing import Prices, session_date
from .events import Event, EventId, EventKind, ToolKind
from .lean.facts import tool_paths
from .lean.grounding import RepositoryGrounding
from .pricing import TOKENS_PER_RATE_UNIT, UnknownModelError
from .context_bundle import SuppliedFragment, read_supplied
from .repository import is_test_module

__all__ = [
    "BundleMeasure",
    "ContextMeasures",
    "CriticalEvidence",
    "CriticalItem",
    "CriticalItemResult",
    "CriticalRecall",
    "FragmentUse",
    "MissingItem",
    "measure_context",
]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EDITS = frozenset({ToolKind.FS_EDIT, ToolKind.FS_WRITE})


@dataclass(frozen=True, order=True)
class CriticalItem:
    """Evidence a task cannot do without: a repository file, optionally one
    symbol it defines."""

    path: str
    symbol: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "symbol": self.symbol}


@dataclass(frozen=True)
class CriticalEvidence:
    """A human-authored list of critical evidence for one session."""

    session_id: str
    items: tuple[CriticalItem, ...]
    #: Where the list came from (a file name), for the report.
    source: str = ""


@dataclass(frozen=True)
class FragmentUse:
    fragment: str
    source: str
    tokens: int
    used_by: tuple[EventId, ...]

    @property
    def used(self) -> bool:
        return bool(self.used_by)

    def as_dict(self) -> dict[str, object]:
        return {
            "fragment": self.fragment,
            "source": self.source,
            "tokens": self.tokens,
            "used": self.used,
            "used_by": list(self.used_by),
        }


@dataclass(frozen=True)
class MissingItem:
    """Needed context the bundle did not hold: a potential defect source."""

    path: str
    symbol: str | None
    #: Edits (or test runs) in the window that depended on it.
    depended_by: tuple[EventId, ...]
    #: Reads of it by the agent in the window: it fetched the context itself.
    read_by: tuple[EventId, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "symbol": self.symbol,
            "depended_by": list(self.depended_by),
            "read_by": list(self.read_by),
        }


@dataclass(frozen=True)
class BundleMeasure:
    bundle: str
    supplied_at: EventId
    fragments: tuple[FragmentUse, ...]
    #: ``session`` (edited and tested files) or ``critical`` (the list).
    needed_basis: str
    needed: tuple[str, ...]
    held: tuple[str, ...]
    missing: tuple[MissingItem, ...]
    #: Model turns after the supply that carried the bundle.
    carried_turns: int
    #: Carrying cost of the unused fragments in USD (``None`` without prices).
    unused_usd: float | None

    @property
    def tokens(self) -> int:
        return sum(f.tokens for f in self.fragments)

    @property
    def unused_tokens(self) -> int:
        return sum(f.tokens for f in self.fragments if not f.used)

    @property
    def precision(self) -> float | None:
        if not self.fragments:
            return None
        return sum(f.used for f in self.fragments) / len(self.fragments)

    @property
    def recall(self) -> float | None:
        if not self.needed:
            return None
        return len(self.held) / len(self.needed)

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle": self.bundle,
            "supplied_at": self.supplied_at,
            "tokens": self.tokens,
            "precision": self.precision,
            "recall": self.recall,
            "needed_basis": self.needed_basis,
            "needed": list(self.needed),
            "held": list(self.held),
            "fragments": [f.as_dict() for f in self.fragments],
            "inventory": {
                "unused_tokens": self.unused_tokens,
                "carried_turns": self.carried_turns,
                "unused_usd": None
                if self.unused_usd is None
                else round(self.unused_usd, 6),
            },
            "missing": [m.as_dict() for m in self.missing],
        }


@dataclass(frozen=True)
class CriticalItemResult:
    item: CriticalItem
    #: The first edit that depended on the item (``None``: none did).
    first_dependent_edit: EventId | None
    #: The supply or read that put it in context before that edit.
    in_context_by: EventId | None

    @property
    def in_context(self) -> bool:
        return self.in_context_by is not None

    def as_dict(self) -> dict[str, object]:
        return {
            **self.item.as_dict(),
            "first_dependent_edit": self.first_dependent_edit,
            "in_context": self.in_context,
            "in_context_by": self.in_context_by,
        }


@dataclass(frozen=True)
class CriticalRecall:
    source: str
    items: tuple[CriticalItemResult, ...]

    @property
    def judged(self) -> tuple[CriticalItemResult, ...]:
        return tuple(i for i in self.items if i.first_dependent_edit is not None)

    @property
    def recall(self) -> float | None:
        judged = self.judged
        if not judged:
            return None
        return sum(i.in_context for i in judged) / len(judged)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "recall": self.recall,
            "judged": len(self.judged),
            "not_depended_on": [
                i.item.as_dict() for i in self.items if i.first_dependent_edit is None
            ],
            "items": [i.as_dict() for i in self.items],
        }


@dataclass(frozen=True)
class ContextMeasures:
    """Context measures of one session."""

    session_id: str
    bundles: tuple[BundleMeasure, ...]
    critical: CriticalRecall | None = None
    #: True when the bundles were not supplied but rebuilt for each prompt.
    simulated: bool = False
    price_book: str | None = None
    priced_on: date | None = None
    unpriced_turns: int = 0
    notes: tuple[str, ...] = field(default=())

    @property
    def unused_tokens(self) -> int:
        return sum(b.unused_tokens for b in self.bundles)

    @property
    def unused_usd(self) -> float | None:
        costs = [b.unused_usd for b in self.bundles]
        if any(c is None for c in costs):
            return None
        return sum(c for c in costs if c is not None)

    def as_dict(self) -> dict[str, object]:
        usd = self.unused_usd
        return {
            "schema": "ter.context-measures/1",
            "session_id": self.session_id,
            "simulated": self.simulated,
            "price_book": self.price_book,
            "priced_on": None if self.priced_on is None else self.priced_on.isoformat(),
            "unpriced_turns": self.unpriced_turns,
            "unused_tokens": self.unused_tokens,
            "unused_usd": None if usd is None else round(usd, 6),
            "bundles": [b.as_dict() for b in self.bundles],
            "critical": None if self.critical is None else self.critical.as_dict(),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Act:
    """What one event did to repository files."""

    index: int
    event_id: EventId
    kind: str  # edit | read | shell
    path: str | None
    text: str
    idents: frozenset[str]


def _edit_text(arguments: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ("new_string", "content"):
        value = arguments.get(key)
        if isinstance(value, str):
            parts.append(value)
    raw = arguments.get("edits")
    if isinstance(raw, list):
        for op in raw:
            if isinstance(op, Mapping) and isinstance(op.get("new_string"), str):
                parts.append(str(op["new_string"]))
    return "\n".join(parts)


def _acts(events: Sequence[Event], g: RepositoryGrounding) -> list[_Act | None]:
    out: list[_Act | None] = []
    for i, e in enumerate(events):
        tool = e.tool
        if e.kind is not EventKind.TOOL_REQUESTED or tool is None:
            out.append(None)
            continue
        if tool.kind in _EDITS or tool.kind is ToolKind.FS_READ:
            named = tool_paths(tool.arguments)
            path = _repo_path(named[0], g) if named else None
            text = _edit_text(tool.arguments) if tool.kind in _EDITS else ""
            kind = "read" if tool.kind is ToolKind.FS_READ else "edit"
            out.append(_Act(i, e.id, kind, path, text, frozenset(_IDENT.findall(text))))
        elif tool.kind is ToolKind.EXEC_SHELL:
            command = tool.arguments.get("command")
            text = command if isinstance(command, str) else e.text
            out.append(
                _Act(i, e.id, "shell", None, text, frozenset(_IDENT.findall(text)))
            )
        else:
            out.append(None)
    return out


def _repo_path(path: str, g: RepositoryGrounding) -> str | None:
    mapped = g.repository_path(path)
    if mapped is not None:
        return mapped
    return path if path in g.files else None


def _names_file(command: str, path: str) -> bool:
    """Whether a shell command names a repository file, by path or by a file
    name that stands as its own word."""
    if path in command:
        return True
    name = PurePosixPath(path).name
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", command) is not None


class _Symbols:
    """Distinctive symbols per file (case-insensitive)."""

    def __init__(self, g: RepositoryGrounding) -> None:
        self.by_file: dict[str, set[str]] = {}
        for name, paths in g.symbols.items():
            for p in paths:
                self.by_file.setdefault(p, set()).add(name.lower())

    def named(self, path: str, idents: frozenset[str]) -> bool:
        mine = self.by_file.get(path)
        if not mine:
            return False
        return any(i.lower() in mine for i in idents)


def _carry(
    events: Sequence[Event], prices: Prices | None, on: date | None
) -> tuple[list[float], list[float], list[int], int]:
    """Per event index: price of ingesting one token on the first priced turn
    after it, price of carrying it through the priced turns after that one,
    and the number of model turns after it."""
    n = len(events)
    first = [0.0] * n
    carry = [0.0] * n
    turns = [0] * n
    acc_first, acc_carry, acc_turns = 0.0, 0.0, 0
    nearest: tuple[float, float] | None = None
    unpriced = 0
    for i in range(n - 1, -1, -1):
        first[i], carry[i], turns[i] = acc_first, acc_carry, acc_turns
        usage = events[i].usage
        if usage is None:
            continue
        acc_turns += 1
        if prices is None:
            continue
        try:
            rates = prices.rate(usage.model, on) if usage.model else None
        except UnknownModelError:
            rates = None
        if rates is None:
            unpriced += 1
            continue
        caching = usage.cache_creation_tokens > 0 or usage.cache_read_tokens > 0
        rate = (
            (rates.cache_write if caching else rates.input) / TOKENS_PER_RATE_UNIT,
            (rates.cache_read if caching else rates.input) / TOKENS_PER_RATE_UNIT,
        )
        if nearest is not None:
            acc_carry += nearest[1]
        nearest = rate
        acc_first = rate[0]
    return first, carry, turns, unpriced


def _dependent(act: _Act, item: CriticalItem, g: RepositoryGrounding) -> bool:
    if act.kind != "edit" or act.path is None:
        return False
    if act.path == item.path:
        return True
    if item.symbol is not None and item.symbol.lower() in {
        i.lower() for i in act.idents
    }:
        return True
    if item.path in g.links.get(act.path, ()):
        return True
    edit = g.edits.get(act.event_id)
    return edit is not None and item.path in edit.links


def measure_context(
    events: Sequence[Event],
    g: RepositoryGrounding,
    *,
    critical: CriticalEvidence | None = None,
    prices: Prices | None = None,
    simulated: bool = False,
) -> ContextMeasures:
    """Precision, recall, carrying cost and missing context of every bundle
    the ``context.supplied`` events of ``events`` record, and the critical
    recall when a list is given (TER-CTX-003, TER-CTX-004, TER-EVD-005)."""
    session_id = events[0].session_id if events else ""
    acts = _acts(events, g)
    symbols = _Symbols(g)
    on = session_date(e.timestamp for e in events)
    first, carry, turns, unpriced = _carry(events, prices, on)

    # Bundles in order of their first supplied fragment.
    supplied: dict[str, list[tuple[int, SuppliedFragment]]] = {}
    for i, e in enumerate(events):
        s = read_supplied(e)
        if s is not None:
            supplied.setdefault(s.bundle, []).append((i, s))
    order = sorted(supplied, key=lambda b: supplied[b][0][0])
    starts = [supplied[b][0][0] for b in order]

    # Critical items: first dependent edit and what was in context before it.
    critical_results: list[CriticalItemResult] = []
    dependent_index: dict[CriticalItem, int] = {}
    if critical is not None:
        first_dep: dict[CriticalItem, _Act] = {}
        by_path: dict[str, EventId] = {}
        before: dict[CriticalItem, EventId | None] = {}
        for i, e in enumerate(events):
            s = read_supplied(e)
            if s is not None:
                by_path.setdefault(s.source, e.id)
            act = acts[i]
            if act is None:
                continue
            if act.kind == "read" and act.path is not None:
                by_path.setdefault(act.path, act.event_id)
            for item in critical.items:
                if item not in first_dep and _dependent(act, item, g):
                    first_dep[item] = act
                    before[item] = by_path.get(item.path)
        for item in critical.items:
            dep = first_dep.get(item)
            critical_results.append(
                CriticalItemResult(
                    item,
                    None if dep is None else dep.event_id,
                    before.get(item) if dep is not None else None,
                )
            )
            if dep is not None:
                dependent_index[item] = dep.index
    test_modules = tuple(p for p in sorted(g.files) if is_test_module(p))

    bundles: list[BundleMeasure] = []
    for k, bundle in enumerate(order):
        start = starts[k]
        end = _window_end(
            events, start, starts[k + 1] if k + 1 < len(starts) else len(events)
        )
        frags = [s for _, s in supplied[bundle]]
        held_sources = {s.source for s in frags}
        window = [a for a in acts[start:end] if a is not None]
        uses: dict[str, list[EventId]] = {s.fragment: [] for s in frags}
        edited: dict[str, list[EventId]] = {}
        tested: dict[str, list[EventId]] = {}
        reads: dict[str, list[EventId]] = {}
        for act in window:
            if act.kind == "read":
                if act.path is not None:
                    reads.setdefault(act.path, []).append(act.event_id)
                continue
            if act.kind == "edit" and act.path is not None and act.path in g.files:
                edited.setdefault(act.path, []).append(act.event_id)
            for s in frags:
                if (
                    (act.kind == "edit" and act.path == s.source)
                    or symbols.named(s.source, act.idents)
                    or (act.kind == "shell" and _names_file(act.text, s.source))
                ):
                    uses[s.fragment].append(act.event_id)
            if act.kind == "shell":
                for t in _tests_named(act.text, test_modules):
                    tested.setdefault(t, []).append(act.event_id)
        if critical is not None:
            basis = "critical"
            needed_items = [
                item
                for item in critical.items
                if start <= dependent_index.get(item, -1) < end
            ]
            needed = sorted({i.path for i in needed_items})
            missing = [
                MissingItem(
                    item.path,
                    item.symbol,
                    tuple(a.event_id for a in window if _dependent(a, item, g)),
                    tuple(reads.get(item.path, ())),
                )
                for item in needed_items
                if item.path not in held_sources
            ]
        else:
            basis = "session"
            depended = {**tested, **edited}
            needed = sorted(depended)
            missing = [
                MissingItem(p, None, tuple(depended[p]), tuple(reads.get(p, ())))
                for p in needed
                if p not in held_sources
            ]
        unused_tokens = sum(s.tokens for s in frags if not uses[s.fragment])
        usd = None if prices is None else unused_tokens * (first[start] + carry[start])
        bundles.append(
            BundleMeasure(
                bundle=bundle,
                supplied_at=events[start].id,
                fragments=tuple(
                    FragmentUse(s.fragment, s.source, s.tokens, tuple(uses[s.fragment]))
                    for s in frags
                ),
                needed_basis=basis,
                needed=tuple(needed),
                held=tuple(p for p in needed if p in held_sources),
                missing=tuple(missing),
                carried_turns=turns[start],
                unused_usd=usd,
            )
        )
    return ContextMeasures(
        session_id=session_id,
        bundles=tuple(bundles),
        critical=None
        if critical is None
        else CriticalRecall(critical.source, tuple(critical_results)),
        simulated=simulated,
        price_book=None if prices is None else prices.name,
        priced_on=on,
        unpriced_turns=unpriced,
    )


def _window_end(events: Sequence[Event], start: int, limit: int) -> int:
    """Where a bundle's window ends: at the next bundle (``limit``), or at
    the first prompt after the supply that follows a tool request (a prompt
    before any tool request is the one the bundle was built ahead of)."""
    acted = False
    for i in range(start + 1, limit):
        kind = events[i].kind
        if kind is EventKind.PROMPT and acted:
            return i
        if kind is EventKind.TOOL_REQUESTED:
            acted = True
    return limit


def _tests_named(command: str, tests: Iterable[str]) -> Iterable[str]:
    """The repository test modules a shell command names."""
    return (t for t in tests if _names_file(command, t))
