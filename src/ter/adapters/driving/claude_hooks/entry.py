"""The hook entry point: raw hook input in, events applied, never an exception out.

Claude Code runs a hook command per event and blocks on it, so this adapter
fails open. Malformed JSON, an unexpected payload shape, or a failure in the
ingest behind it (a full disk, a read-only log directory) all become an
``ignored`` result; the agent session is never interrupted by TER.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import IO

from ....domain.stream import Signals
from ....ports.driven import Clock
from ....ports.driving import EventIngest
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
) -> HookResult:
    """Read one payload from ``stdin``, handle it, and print the hook output.

    Always writes :data:`HOOK_OUTPUT` and returns; the caller exits 0.
    """
    try:
        raw = stdin.read()
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        result = HookResult(HookStatus.IGNORED, reason=f"unreadable stdin: {error}")
    else:
        result = handle_hook(raw, ingest, clock=clock)
    stdout.write(HOOK_OUTPUT + "\n")
    return result
