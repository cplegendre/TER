"""Repository-wide pytest configuration.

Registers the requirement-traceability plugin, which adds ``--req-trace``
(see docs/ter4/requirements.md).
"""

pytest_plugins = ("ter.adapters.driving.pytest_req", "pytester")
