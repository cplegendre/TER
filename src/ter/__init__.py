"""TER 4: Lean analysis for agentic software engineering.

The package is a hexagon of ports and adapters:

* ``ter.domain`` holds the pure Lean model. It imports nothing outside itself
  except the standard library and numpy, and performs no IO.
* ``ter.ports`` declares the Protocols the application needs from the outside
  world (driven) and offers to it (driving).
* ``ter.application`` holds use cases that orchestrate the domain through ports.
* ``ter.adapters`` is the only code that knows about Claude Code, tokenizers,
  embedding models, files or databases.
* ``ter.bootstrap`` is the composition root that wires adapters to ports.

Dependencies point inward only; ``lint-imports`` enforces this in CI. The
TER 3 package, ``ter_calculator``, stays outside the hexagon and is wrapped by
adapters until each capability is rebuilt inside it (a strangler-fig rebuild).
"""

from __future__ import annotations

from .domain.events import EVENT_SCHEMA_VERSION

__all__ = ["EVENT_SCHEMA_VERSION"]
