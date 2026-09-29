# ADR 0002: Freeze TER 3 behaviour with hermetic golden snapshots

- Status: accepted
- Date: 2026-09-29

## Context

TER 3's scores depend on tiktoken's `cl100k_base` encoding and the
`all-MiniLM-L12-v2` sentence-transformers model. Both are downloaded at first
use, and embedding values vary slightly across torch versions and CPUs. A
golden test built on them would need network access and could fail for
reasons unrelated to TER's logic.

## Decision

Golden tests pin both seams in `ter_calculator.embedding_cache` to TER 4's
deterministic offline adapters: `RegexTokenizer` for token counts and
`HashingEmbedder` (BLAKE2b feature hashing) for embeddings. With those fixed,
the snapshots freeze everything TER computes on top of them: span
segmentation, classification, repetition scoring, waste patterns, the ratio,
economics and input analysis.

Snapshots are JSON under `tests/golden/snapshots`, regenerated only with
`TER_UPDATE_GOLDEN=1`.

## Consequences

- Golden tests run offline, in about half a second, on every platform.
- They characterise analysis logic, not the embedding model. Semantic quality
  with the real model remains covered by the existing unit and BDD tests and,
  from L2, by the calibration benchmark.
- The two adapters double as TER 4's offline fallback, so the test double is
  production code held to the same contracts.
