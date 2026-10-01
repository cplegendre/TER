# ADR 0005: Admitting external capabilities as capability packs

- Status: accepted
- Date: 2026-10-01

## Context

Other projects already do things TER 4 needs. GARE, the first of them, records
agent usage and governance data that overlaps TER's observation, cost and
policy levels (L1, L4, L5). The TER × GARE integration plan asks how such
work enters TER without turning TER into a federation of codebases, without
TER depending on another project's release cycle, and without losing the
rules that keep TER honest: the hexagon's dependency rule (ADR 0001), EARS
requirements traced to tests, and the 200 vision points as the root of every
requirement.

Three ways in were considered:

1. **Depend on the other package** and call it from TER. Fast, but TER would
   then install, import and test only with that package and its stack
   (`pydantic`, `httpx`, a database driver), and its types would leak into
   ports and use cases.
2. **Vendor its code** into `src/ter`. TER would own code written under
   different rules: no EARS requirements, no contract suites, untyped or
   differently typed, with no record of why each part exists.
3. **Re-specify the capability under TER's rules** and read the other
   system's data at a file boundary. Slower to start, but TER stays one
   hexagon with one set of gates.

## Decision

1. **TER stays the single hexagon.** There is one domain, one set of ports
   and one composition root. An external project contributes concepts and
   data, never a second core.
2. **An external capability enters as a capability pack**, which has five
   parts, all inside this repository:
   - a **port**: a `typing.Protocol` in `ter.ports` whose docstring lists its
     obligations (what every implementation guarantees);
   - a **contract suite** in `tests/contract/` that turns each obligation
     into a test, run against every adapter and the fake;
   - **EARS requirements** in `requirements/*.yaml`, each linked to a vision
     point through `source_points`, starting `planned` and becoming
     `verified` when a passing test cites them;
   - an **in-memory fake** that passes the contract suite, so use cases and
     other tests need nothing outside the repository;
   - one **reference adapter**, registered through a package entry point
     (group `ter.capabilities`, discovered by `ter.bootstrap`; the loader
     lands with the first pack), so installing the adapter's extra is what
     turns the capability on.
3. **Coupling is by file contract, not by import.** An adapter for an outside
   system reads that system's files or SQLite rows by their published schema
   name (for GARE, for example, `gare.ter.usage.v2`) and maps them to TER
   domain types. It never imports the outside system's package, so TER
   installs and its full test suite runs without it. The contract suite pins
   the schema with fixture files, and the adapter rejects a schema name it
   does not know. The adapter ships as an optional extra
   (`pip install 'ter-calculator[gare]'`), adding only what it needs to read
   the files.
4. **The import rule is enforced.** `gare`, `pydantic` and `httpx` are in the
   forbidden lists of the `pure-domain` (`ter.domain`) and
   `vendor-free-core` (`ter.ports`, `ter.application`) import-linter
   contracts in `pyproject.toml`, checked by `lint-imports` in CI and by
   `tests/architecture/test_import_contracts.py` (TER-ARC-003). Only an
   adapter module may depend on what a pack's extra installs.
5. **Concepts are re-specified, not vendored.** A capability's behaviour is
   written again as EARS requirements and implemented under TER's rules:
   strict typing, dependencies pointing inward, golden snapshots for any
   scoring change, prices as data (ADR 0003). No source file is copied from
   the outside project.
6. **New vision points carry their origin.** A goal the outside project
   brings that Leigh's list does not already state becomes a point past P200
   in `requirements/points.yaml`, with an `origin` (`source`, `ref`,
   `author`). P001 to P200 stay Leigh's verbatim text and record no origin.
   `ter-req lint` enforces both (TER-REQ-009, TER-REQ-010), rejects a
   requirement citing a point that is not catalogued (TER-REQ-012), and
   `docs/ter4/points.md` shows every point's origin (TER-REQ-011).
7. **GARE is the first import and sets the pattern.** Its packs (usage
   records first) follow this ADR; later external capabilities follow GARE's
   packs as worked examples.

## Consequences

- TER installs, type-checks and passes every gate with no external project
  present. A missing extra means the capability is absent, not broken.
- An external project can change its internals freely; only a change to a
  published schema that TER reads needs a new adapter version, and the
  contract suite shows exactly what changed.
- Every imported behaviour is visible in the requirement catalogue and the
  points index, with its origin, and gated like TER's own.
- Re-specifying costs more up front than importing. That is the price of one
  set of rules; packs are small (one port each) to keep it down.
- Points past P200 make the catalogue larger than Leigh's list. The level and
  points summaries count them; the vision point trace in `ter-req report`
  still reports coverage of P001 to P200.

## Related

- [ADR 0001](0001-hexagonal-strangler-rebuild.md): the hexagon and its
  dependency rule.
- [Contributing guide](../guides/contributing.md) and
  [definition of done](../guides/definition-of-done.md): how a pack and a
  contributed point are added and when they are done.
- [docs/ter4/requirements.md](../ter4/requirements.md): the EARS catalogue
  and its controls.
