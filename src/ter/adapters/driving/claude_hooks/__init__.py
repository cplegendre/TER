"""Claude Code hooks as a driving adapter: hook JSON in, ``ter.event`` out.

See ``docs/ter4/l1-observed.md`` for the hook-to-event table.
"""

from __future__ import annotations

from .entry import HOOK_OUTPUT, HookResult, handle_hook, run_hook
from .translate import LIFECYCLE_HOOKS, HookStatus, HookTranslation, translate

__all__ = [
    "HOOK_OUTPUT",
    "LIFECYCLE_HOOKS",
    "HookResult",
    "HookStatus",
    "HookTranslation",
    "handle_hook",
    "run_hook",
    "translate",
]
