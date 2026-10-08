"""GARE runs as a :class:`~ter.ports.driven.SessionSource` (issue #52).

GARE (General Agent Routing & Execution) routes each task of a run to a
provider and model, fails over when a route fails, and, in mission mode,
repairs and verifies patches. TER reads what GARE's own commands export,
never its database or its package:

- ``gare export-ter --run RUN -o gare-ter-usage.jsonl``: one row per model
  call, schema ``gare.ter.usage.v2`` (tokens, latency, success).
- ``gare explain RUN --json > explain.json``: the run, its tasks, errors and
  run events (route decisions, task starts, verification steps, final state).

A reference is a folder holding either or both files, or one of the files.
The run becomes one session:

========================  ==========================  =========
GARE                      ter.event kind              actor
========================  ==========================  =========
run event ``created``     ``intent.stated``           user
``route_decision``        ``route.selected``          system
``task_started``, first   ``attempt.started``         assistant
route of a mission
coder attempt
usage row with tokens     ``response`` (with usage)   assistant
usage row, no tokens,     ``route.failover``          system
not successful
verification steps        ``verification.completed``  system
final state with score    ``outcome.recorded``        system
final state               ``task.completed``          system
========================  ==========================  =========

GARE reports input and output tokens only, so every trace carries the
``no-cache-tokens`` usage limit and reports say so (TER-SRC-013). Run event
states TER knows but does not model (planning notes, diagnoses) produce no
event; unknown states and rows of an unknown schema are counted as
unrecognised, so coverage stays honest (TER-SRC-012).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ....domain.events import (
    Actor,
    Event,
    EventKind,
    Provenance,
    SessionTrace,
    TokenUsage,
    UnrecognisedRecord,
    make_event_id,
)

__all__ = ["EXPLAIN_FILE", "GARE_USAGE_SCHEMA", "NO_CACHE_TOKENS", "GareRunSource"]

GARE_USAGE_SCHEMA = "gare.ter.usage.v2"
#: The usage limit every GARE trace carries.
NO_CACHE_TOKENS = "no-cache-tokens"
#: File name ``gare explain RUN --json`` output is looked for under.
EXPLAIN_FILE = "explain.json"

#: Run event states that verify an attempt, and how each says it passed.
_VERIFICATIONS = frozenset(
    {
        "acceptance_evaluated",
        "attempt_complete",
        "runtime_qa",
        "browser_qa",
        "persona_review",
    }
)
#: States that end a run (orchestrator and mission modes).
_FINAL = frozenset({"succeeded", "partial", "failed", "needs_review", "blocked"})
#: States TER knows and deliberately leaves out of the event stream.
_IGNORED = frozenset(
    {
        "execution_mode",
        "planned",
        "resumed",
        "worktree_ready",
        "acceptance_contract_loaded",
        "note",
        "repair_stopped",
        "tool_output_repaired",
        "syntax_feedback",
        "lsp_diagnostics",
        "diagnosis",
    }
)
#: Mission coder task ids carry their attempt number.
_MISSION_ATTEMPT = re.compile(r"mission-coder-(\d+)-")


@dataclass(frozen=True)
class _Draft:
    """An event before its sequence, id chain and order are fixed."""

    at: datetime | None
    order: tuple[int, int, int]
    kind: EventKind
    actor: Actor
    text: str
    source: str
    key: str
    fingerprint: str
    usage: TokenUsage | None = None


class GareRunSource:
    """Reads one exported GARE run."""

    format_name = "gare-run"

    @staticmethod
    def accepts(ref: str | Path) -> bool:
        """True when ``ref`` looks like a GARE export, without reading it all."""
        path = Path(ref)
        if path.is_dir():
            return (path / EXPLAIN_FILE).is_file() or any(
                _is_usage_file(p) for p in sorted(path.glob("*.jsonl"))
            )
        if path.suffix == ".jsonl":
            return _is_usage_file(path)
        if path.suffix == ".json" and path.is_file():
            return _explain_shape(_load_json(path))
        return False

    def read(self, ref: str | Path) -> SessionTrace:
        path = Path(ref)
        if not path.exists():
            raise FileNotFoundError(path)
        explain_path, usage_paths = _files(path)
        explain = _load_json(explain_path) if explain_path else None
        if explain is not None and not _explain_shape(explain):
            raise ValueError(f"{explain_path} is not `gare explain --json` output")

        unrecognised: list[UnrecognisedRecord] = []
        rows: list[tuple[str, int, dict[str, Any]]] = []
        for usage_path in usage_paths:
            for line, row in _jsonl(usage_path):
                if row is None or row.get("schema") != GARE_USAGE_SCHEMA:
                    schema = row.get("schema") if row is not None else None
                    unrecognised.append(
                        UnrecognisedRecord(line, str(schema or "invalid-json"))
                    )
                    continue
                rows.append((usage_path.name, line, row))

        run_id = _run_id(explain, rows, path)
        drafts: list[_Draft] = []
        if explain is not None:
            drafts += _run_events(explain, unrecognised)
        usage_rows = [r for r in rows if r[2].get("run_id") == run_id]
        if usage_rows:
            drafts += _usage_events(usage_rows, explain)
        elif explain is not None:
            # Without the usage export, each task's final route stands in.
            drafts += _task_events(explain)

        drafts.sort(key=lambda d: (_sort_time(d.at), d.order))
        events: list[Event] = []
        for draft in drafts:
            event_id = make_event_id(
                "gare", run_id, draft.source, draft.key, draft.kind.value
            )
            events.append(
                Event(
                    id=event_id,
                    session_id=run_id,
                    sequence=len(events),
                    kind=draft.kind,
                    actor=draft.actor,
                    text=draft.text,
                    provenance=Provenance(
                        source=draft.source,
                        record_id=f"{draft.source}:{draft.key}",
                        fingerprint=draft.fingerprint,
                    ),
                    timestamp=draft.at,
                    usage=draft.usage,
                    parent_id=events[-1].id if events else None,
                )
            )
        return SessionTrace(
            session_id=run_id,
            source_format=self.format_name,
            events=tuple(events),
            unrecognised=tuple(unrecognised),
            usage_limits=(NO_CACHE_TOKENS,),
        )


# -- files ----------------------------------------------------------------


def _files(path: Path) -> tuple[Path | None, list[Path]]:
    if path.is_dir():
        explain = path / EXPLAIN_FILE
        usage = [p for p in sorted(path.glob("*.jsonl")) if _is_usage_file(p)]
        if not explain.is_file() and not usage:
            raise ValueError(
                f"{path} holds neither {EXPLAIN_FILE} nor a {GARE_USAGE_SCHEMA} file"
            )
        return (explain if explain.is_file() else None), usage
    if path.suffix == ".jsonl":
        return None, [path]
    return path, []


def _is_usage_file(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    return (
                        isinstance(row, dict) and row.get("schema") == GARE_USAGE_SCHEMA
                    )
    except (OSError, ValueError):
        return False
    return False


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _explain_shape(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("run"), Mapping)
        and isinstance(value.get("events"), list)
        and isinstance(value.get("tasks"), list)
    )


def _jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any] | None]]:
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                yield number, None
                continue
            yield number, value if isinstance(value, dict) else None


def _run_id(
    explain: Mapping[str, Any] | None,
    rows: list[tuple[str, int, dict[str, Any]]],
    path: Path,
) -> str:
    if explain is not None:
        return str(explain["run"].get("id") or "")
    runs = sorted({str(r[2].get("run_id")) for r in rows})
    if len(runs) == 1:
        return runs[0]
    if not runs:
        raise ValueError(f"{path} holds no {GARE_USAGE_SCHEMA} rows")
    raise ValueError(
        f"{path} holds {len(runs)} runs ({', '.join(runs)}); export one with "
        "`gare export-ter --run RUN`"
    )


# -- run events -------------------------------------------------------------


def _run_events(
    explain: Mapping[str, Any], unrecognised: list[UnrecognisedRecord]
) -> Iterator[_Draft]:
    goal = str(explain["run"].get("goal") or "")
    attempts: set[int] = set()
    for index, event in enumerate(explain["events"]):
        if not isinstance(event, Mapping):
            unrecognised.append(UnrecognisedRecord(index + 1, "run_event:invalid"))
            continue
        state = str(event.get("state") or "")
        raw_detail = event.get("detail")
        detail: Mapping[str, Any] = (
            raw_detail if isinstance(raw_detail, Mapping) else {}
        )
        at = _time(event.get("created_at"))
        fingerprint = _fingerprint(event)

        def draft(
            kind: EventKind,
            actor: Actor,
            text: str,
            sub: int = 0,
            *,
            index: int = index,
            at: datetime | None = at,
            fingerprint: str = fingerprint,
        ) -> _Draft:
            return _Draft(
                at=at,
                order=(0, index, sub),
                kind=kind,
                actor=actor,
                text=text,
                source=EXPLAIN_FILE,
                key=f"event{index}",
                fingerprint=fingerprint,
            )

        if state == "created":
            yield draft(EventKind.PROMPT, Actor.USER, str(detail.get("goal") or goal))
        elif state == "route_decision":
            task = str(detail.get("task_id") or "")
            yield draft(
                EventKind.ROUTE_SELECTED, Actor.SYSTEM, _route_text(task, detail)
            )
            match = _MISSION_ATTEMPT.match(task)
            if match and int(match.group(1)) not in attempts:
                attempts.add(int(match.group(1)))
                yield draft(
                    EventKind.ATTEMPT_STARTED,
                    Actor.ASSISTANT,
                    f"attempt {match.group(1)} {task}",
                    1,
                )
        elif state == "task_started":
            task = str(detail.get("task_id") or "")
            yield draft(EventKind.ATTEMPT_STARTED, Actor.ASSISTANT, f"attempt 1 {task}")
        elif state in _VERIFICATIONS:
            yield draft(
                EventKind.VERIFICATION_COMPLETED,
                Actor.SYSTEM,
                _verification_text(state, detail),
            )
        elif state in _FINAL:
            score = detail.get("score")
            if isinstance(score, int | float) and not isinstance(score, bool):
                yield draft(
                    EventKind.OUTCOME_RECORDED,
                    Actor.SYSTEM,
                    f"{state}: score {score:g}/100",
                )
            yield draft(EventKind.TASK_COMPLETED, Actor.SYSTEM, f"run {state}", 1)
        elif state not in _IGNORED:
            unrecognised.append(UnrecognisedRecord(index + 1, f"run_event:{state}"))


def _route_text(task: str, detail: Mapping[str, Any]) -> str:
    candidates = [c for c in detail.get("candidates") or [] if isinstance(c, Mapping)]
    if not candidates:
        return f"{task}: no route"
    first = candidates[0]
    text = f"{task}: {first.get('provider')}/{first.get('model')}"
    if len(candidates) > 1:
        text += f" (of {len(candidates)} candidates)"
    return text


def _verification_text(state: str, detail: Mapping[str, Any]) -> str:
    if state == "persona_review":
        verdict = str(detail.get("verdict") or "unknown")
        passed: bool | None = verdict == "pass" if verdict != "unknown" else None
    elif state == "attempt_complete":
        passed = detail.get("test_exit_code") == 0 and bool(detail.get("review_pass"))
    else:
        value = detail.get("passed")
        passed = bool(value) if value is not None else None
    result = "unknown" if passed is None else ("pass" if passed else "fail")
    attempt = detail.get("attempt")
    prefix = f"attempt {attempt} " if attempt is not None else ""
    return f"{prefix}{state}: {result}"


# -- usage --------------------------------------------------------------------


def _usage_events(
    rows: Iterable[tuple[str, int, dict[str, Any]]],
    explain: Mapping[str, Any] | None,
) -> Iterator[_Draft]:
    errors = _errors(explain)
    for source, line, row in rows:
        task = str(row.get("task_id") or "")
        route = f"{row.get('provider')}/{row.get('model')}"
        tokens = TokenUsage(
            input_tokens=_int(row.get("input_tokens")),
            output_tokens=_int(row.get("output_tokens")),
        )
        failed = not row.get("success") and tokens.total == 0
        if failed:
            code = errors.get((task, str(row.get("provider")), str(row.get("model"))))
            text = f"{task}: {route} failed" + (f" ({code})" if code else "")
        else:
            text = f"{task}: {route}"
        yield _Draft(
            at=_time(row.get("created_at")),
            order=(1, line, 0),
            kind=EventKind.ROUTE_FAILOVER if failed else EventKind.RESPONSE,
            actor=Actor.SYSTEM if failed else Actor.ASSISTANT,
            text=text,
            source=source,
            key=f"line{line}",
            fingerprint=_fingerprint(row),
            usage=None if failed else tokens,
        )


def _task_events(explain: Mapping[str, Any]) -> Iterator[_Draft]:
    for index, task in enumerate(explain["tasks"]):
        if not isinstance(task, Mapping) or not task.get("provider"):
            continue
        yield _Draft(
            at=_time(task.get("finished_at") or task.get("created_at")),
            order=(1, index, 0),
            kind=EventKind.RESPONSE,
            actor=Actor.ASSISTANT,
            text=f"{task.get('id')}: {task.get('provider')}/{task.get('model')}",
            source=EXPLAIN_FILE,
            key=f"task{index}",
            fingerprint=_fingerprint(task),
            usage=TokenUsage(
                input_tokens=_int(task.get("input_tokens")),
                output_tokens=_int(task.get("output_tokens")),
            ),
        )


def _errors(explain: Mapping[str, Any] | None) -> dict[tuple[str, str, str], str]:
    found: dict[tuple[str, str, str], str] = {}
    if explain is None:
        return found
    for error in explain.get("errors") or []:
        if not isinstance(error, Mapping):
            continue
        detail = error.get("detail") or error.get("detail_json") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        if not isinstance(detail, Mapping):
            detail = {}
        key = (
            str(error.get("task_id") or ""),
            str(detail.get("provider")),
            str(detail.get("model")),
        )
        found.setdefault(key, str(error.get("code") or ""))
    return found


# -- values -------------------------------------------------------------------


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _sort_time(value: datetime | None) -> float:
    return value.timestamp() if value is not None else float("-inf")


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
