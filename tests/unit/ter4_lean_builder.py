"""A small script builder for ``ter.event`` streams used by the L2 tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ter.domain import (
    Actor,
    Event,
    EventKind,
    Provenance,
    ToolCall,
    ToolKind,
    make_event_id,
)

_ACTOR = {
    EventKind.PROMPT: Actor.USER,
    EventKind.TOOL_COMPLETED: Actor.TOOL,
    EventKind.ROUTE_FAILOVER: Actor.SYSTEM,
}


class Script:
    """Append events in session order; each event is one second after the last."""

    def __init__(self, session: str = "s", *, timed: bool = True) -> None:
        self.session = session
        self.events: list[Event] = []
        self._timed = timed
        self._calls = 0
        self._start = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    def _add(
        self,
        kind: EventKind,
        text: str = "",
        tool: ToolCall | None = None,
    ) -> Event:
        n = len(self.events)
        event = Event(
            id=make_event_id(self.session, n, kind.value),
            session_id=self.session,
            sequence=n,
            kind=kind,
            actor=_ACTOR.get(kind, Actor.ASSISTANT),
            text=text,
            provenance=Provenance(source="script", record_id=f"r{n}"),
            timestamp=self._start + timedelta(seconds=n) if self._timed else None,
            tool=tool,
        )
        self.events.append(event)
        return event

    def prompt(self, text: str) -> Event:
        return self._add(EventKind.PROMPT, text)

    def think(self, text: str) -> Event:
        return self._add(EventKind.REASONING, text)

    def say(self, text: str) -> Event:
        return self._add(EventKind.RESPONSE, text)

    def failover(self, text: str) -> Event:
        """A model route that failed and returned nothing (``route.failover``)."""
        return self._add(EventKind.ROUTE_FAILOVER, text)

    def lifecycle(self, kind: EventKind, text: str = "") -> Event:
        return self._add(kind, text)

    def call(
        self,
        native: str,
        kind: ToolKind,
        args: dict[str, Any],
        output: str | None = "ok",
    ) -> tuple[Event, Event | None]:
        self._calls += 1
        call_id = f"c{self._calls}"
        request = self._add(
            EventKind.TOOL_REQUESTED,
            str(sorted(args.items())),
            ToolCall(native, kind, call_id, args),
        )
        if output is None:
            return request, None
        result = self._add(
            EventKind.TOOL_COMPLETED, output, ToolCall(native, kind, call_id)
        )
        return request, result

    def complete(self, request: Event, output: str = "ok") -> Event:
        """The result of a request made with ``output=None``: lets a script
        issue several calls in one model turn (parallel tool calls) before
        their results arrive, as a transcript records them."""
        assert request.tool is not None
        tool = request.tool
        return self._add(
            EventKind.TOOL_COMPLETED,
            output,
            ToolCall(tool.native_name, tool.kind, tool.call_id),
        )

    def read(
        self, path: str, output: str = "x = 1\n", **extra: Any
    ) -> tuple[Event, Event | None]:
        return self.call("Read", ToolKind.FS_READ, {"file_path": path, **extra}, output)

    def search(
        self, pattern: str, output: str = "src/a.py:1:x"
    ) -> tuple[Event, Event | None]:
        return self.call("Grep", ToolKind.FS_SEARCH, {"pattern": pattern}, output)

    def edit(
        self, path: str, old: str = "a", new: str = "b", output: str = "updated"
    ) -> tuple[Event, Event | None]:
        return self.call(
            "Edit",
            ToolKind.FS_EDIT,
            {"file_path": path, "old_string": old, "new_string": new},
            output,
        )

    def write(
        self, path: str, content: str, output: str = "written"
    ) -> tuple[Event, Event | None]:
        return self.call(
            "Write", ToolKind.FS_WRITE, {"file_path": path, "content": content}, output
        )

    def bash(
        self, command: str, output: str | None = "ok"
    ) -> tuple[Event, Event | None]:
        return self.call("Bash", ToolKind.EXEC_SHELL, {"command": command}, output)

    def todo(self, item: str) -> tuple[Event, Event | None]:
        return self.call(
            "TodoWrite", ToolKind.PLAN, {"todos": [{"content": item}]}, "Todos updated"
        )

    def task(
        self, description: str, prompt: str, output: str = "done"
    ) -> tuple[Event, Event | None]:
        return self.call(
            "Task",
            ToolKind.AGENT_HANDOFF,
            {"description": description, "prompt": prompt},
            output,
        )


FAIL = "FAILED tests/test_a.py::test_x - AssertionError: assert 1 == 2\n1 failed, 3 passed in 0.12s"
FAIL_OTHER = (
    "FAILED tests/test_a.py::test_y - KeyError: 'k'\n1 failed, 3 passed in 0.10s"
)
PASS = "4 passed in 0.10s"
