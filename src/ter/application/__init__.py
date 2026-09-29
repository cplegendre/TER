"""Use cases that orchestrate the domain through ports.

Empty at L0 by design: the TER 3 pipeline is still reached through the
``ter_calculator`` package while its behaviour is frozen by the golden tests
in ``tests/golden``. Use cases move here one at a time as each capability is
rebuilt against the ports.
"""
