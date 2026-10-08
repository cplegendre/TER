"""The hook entry point: raw hook input in, events applied, never an exception out.

Claude Code runs a hook command per event and blocks on it, so this adapter
fails open. Malformed JSON, an unexpected payload shape, or a failure in the
ingest behind it (a full disk, a read-only log directory) all become an
``ignored`` result; the agent session is never interrupted by TER.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import IO

from ....domain.stream import Signals
from ....ports.driven import Clock
from ....ports.driving import EventIngest
from .record import record_payload
from .translate import HookStatus, HookTranslation, translate

__all__ = ["HookResult", "handle_hook", "run_hook"]

#: What the hook prints for Claude Code: an empty object changes nothing.
HOOK_OUTPUT = "{}"


@dataclass(frozen=True)
class HookResult:
    """What handling one hook payload did."""

    status: HookStatus
    hook_event_name: str | None = None
    session_id: str | None = None
    appended: int = 0
    signals: tuple[Signals, ...] = ()
    reason: str = ""
    #: Where the raw payload was recorded, when recording was asked for.
    recorded_to: Path | None = None
    #: Why recording failed; the payload is still handled.
    record_error: str = ""


def handle_hook(
    raw: str | bytes | Mapping[str, object],
    ingest: EventIngest | Callable[[], EventIngest],
    *,
    clock: Clock | None = None,
) -> HookResult:
    """Translate one hook payload and apply its events to ``ingest``.

    ``ingest`` may be a factory, called only when there are events to apply,
    so lifecycle hooks and bad input never touch the event log.
    """
    try:
        payload: object = raw if isinstance(raw, Mapping) else json.loads(raw)
    except (ValueError, TypeError) as error:
        return HookResult(HookStatus.IGNORED, reason=f"invalid JSON: {error}")
    try:
        received_at: datetime | None = clock.now() if clock is not None else None
        translation = translate(payload, received_at=received_at)
        if translation.status is not HookStatus.RECORDED:
            return _unrecorded(translation)
        sink = ingest if isinstance(ingest, EventIngest) else ingest()
        signals = tuple(sink.apply(event) for event in translation.events)
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return HookResult(HookStatus.IGNORED, reason=f"{type(error).__name__}: {error}")
    return HookResult(
        HookStatus.RECORDED,
        translation.hook_event_name,
        translation.session_id,
        appended=sum(1 for s in signals if s.accepted),
        signals=signals,
    )


def _unrecorded(translation: HookTranslation) -> HookResult:
    return HookResult(
        translation.status,
        translation.hook_event_name,
        translation.session_id,
        reason=translation.reason,
    )


def run_hook(
    stdin: IO[str],
    stdout: IO[str],
    ingest: EventIngest | Callable[[], EventIngest],
    *,
    clock: Clock | None = None,
    record_to: Path | None = None,
) -> HookResult:
    """Read one payload from ``stdin``, handle it, and print the hook output.

    With ``record_to``, the raw payload is first recorded there (see
    :mod:`.record`). Always writes :data:`HOOK_OUTPUT` and returns; the caller
    exits 0.
    """
    try:
        raw = stdin.read()
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        result = HookResult(HookStatus.IGNORED, reason=f"unreadable stdin: {error}")
    else:
        if record_to is None:
            result = handle_hook(raw, ingest, clock=clock)
        else:
            # Read the clock once, so a recording replays to the very ids
            # (prompts and stops include the second received) seen live.
            moment, clock_error = _read_clock(clock)
            recorded_to, record_error = _record(raw, record_to, moment)
            result = replace(
                handle_hook(raw, ingest, clock=_At(moment) if moment else None),
                recorded_to=recorded_to,
                record_error=clock_error or record_error,
            )
    stdout.write(HOOK_OUTPUT + "\n")
    return result


@dataclass(frozen=True)
class _At:
    """A clock stopped at one moment."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


def _read_clock(clock: Clock | None) -> tuple[datetime | None, str]:
    try:
        return (clock.now() if clock is not None else None), ""
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return None, f"clock: {type(error).__name__}: {error}"


def _record(
    raw: str, directory: Path, received_at: datetime | None
) -> tuple[Path | None, str]:
    try:
        return record_payload(raw, directory, received_at=received_at), ""
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return None, f"{type(error).__name__}: {error}"
