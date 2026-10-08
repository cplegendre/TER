"""Claude Code hooks as a driving adapter: hook JSON in, ``ter.event`` out.

See ``docs/ter4/l1-observed.md`` for the hook-to-event table.
"""

from __future__ import annotations

from .entry import HOOK_OUTPUT, HookResult, handle_hook, run_hook
from .record import RECORDING_SCHEMA, Recording, read_recordings, record_payload
from .translate import LIFECYCLE_HOOKS, HookStatus, HookTranslation, translate

__all__ = [
    "HOOK_OUTPUT",
    "LIFECYCLE_HOOKS",
    "RECORDING_SCHEMA",
    "HookResult",
    "HookStatus",
    "HookTranslation",
    "Recording",
    "handle_hook",
    "read_recordings",
    "record_payload",
    "run_hook",
    "translate",
]
