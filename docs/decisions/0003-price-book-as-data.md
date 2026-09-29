# ADR 0003: Model prices are dated data behind a PriceBook port

- Status: accepted
- Date: 2026-09-29

## Context

TER 3 hard-codes token prices in three places: the `CostModel` dataclass
defaults, `parse_cost_model("sonnet")`, and the haiku/sonnet/opus tiers in
`cost_model.PRICING`. Vendors change prices on their own schedule, a session
from last year should be costed at last year's prices, and nobody can tell
from the code where a number came from or when it was last checked. Changing
a price meant a code release.

## Decision

- The domain owns what a price is and how it is used: `Rates` (USD per
  million tokens for input, output, cache reads and cache writes; thinking
  billed as output), `PriceSchedule` (resolve a model name or alias and a
  date to the entry then in effect), and the cost arithmetic `token_cost` /
  `usage_cost`, in TER 3's operation order so dollar figures are unchanged.
- A driven port, `PriceBook.rate(model, at=None) -> Rates`, supplies prices.
- The shipped adapter, `JsonPriceBook`, reads `ter/data/price_book.json`
  (schema `ter.price_book/1`, package data). Each entry carries a model id,
  aliases, an `effective_from` date and a mandatory `source` note.
  `InMemoryPriceBook` is the fake; both pass `tests/contract/test_price_book.py`.
- TER 3 reads its rates through the adapter. The initial book holds exactly
  the TER 3.0.0 rates, so behaviour is unchanged; the golden snapshots and
  `tests/unit/test_ter4_pricing.py` prove it.

## Consequences

- A price change is a data change with a date and a source, reviewable on its
  own; old sessions can be costed with the prices in effect at the time.
- The initial `effective_from` (2025-01-01) is a floor chosen so existing
  sessions resolve; the TER 3 rates were carried over as-is and have not been
  re-verified against published list prices. Correcting them is a deliberate,
  dated data change that will show up in golden economics snapshots.
- TER 3 importing the pricing adapter means the `independent-adapters`
  contract ignores chains that pass through `ter_calculator` to it.
