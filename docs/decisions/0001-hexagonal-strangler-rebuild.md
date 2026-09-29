# ADR 0001: Rebuild TER as a hexagon, by strangler fig

- Status: accepted
- Date: 2026-09-29

## Context

TER 3 couples analysis to Claude Code's JSONL format, to tiktoken and to one
embedding model, in one package. TER 4 must support other agent harnesses,
live and offline analysis with the same semantics, and capabilities staged as
maturity levels. A rewrite in one step would leave no working TER for weeks
and no way to prove the new code scores sessions the same way.

## Decision

- New code lives in a new package, `ter`, laid out as domain, ports,
  application, adapters and bootstrap. Dependencies point inward only, enforced
  by import-linter contracts in CI.
- TER 3 (`ter_calculator`) stays outside the hexagon. Adapters may wrap it; the
  domain, ports and use cases may not import it.
- Capabilities move into the hexagon one at a time. Each move is a separate,
  mergeable change that must leave the golden snapshots untouched.
- TER 3 import paths remain as shims for one major version once their code
  has moved.

## Consequences

- Two packages ship in one distribution during the migration.
- Every behaviour change to scoring shows up as a golden snapshot diff that
  must be committed deliberately.
- A second harness needs only new `SessionSource` (and later
  `InterventionChannel`) adapters.
