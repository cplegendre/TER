"""L3 context bundles: selection, determinism and budget (TER-CTX-001), the
``context.supplied`` events (TER-EVD-004), precision and recall
(TER-CTX-003), critical recall (TER-EVD-005), and unused context as
inventory with missing context as defect risk (TER-CTX-004), on the
synthetic shop repository (``ter4_shop_repo``)."""

from __future__ import annotations

import io
import json
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from ter4_lean_builder import PASS, Script
from ter4_shop_repo import SHOP, at, shop_repo

from ter import bootstrap
from ter.adapters.driven.critical_evidence import (
    CriticalEvidenceError,
    read_critical_evidence,
)
from ter.adapters.driven.event_log import JsonlEventLog
from ter.adapters.driven.event_log.codec import event_from_record, event_to_record
from ter.adapters.driven.in_memory import (
    FixedClock,
    InMemoryEventLog,
    InMemoryPriceBook,
)
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import main
from ter.application.context import ContextBuilder, MeasureContext, SupplyContext
from ter.application.ground import ground_session
from ter.bootstrap.capabilities import repository_evidence
from ter.domain import Event, PriceEntry, Rates, TokenUsage
from ter.domain.context_bundle import (
    ContextBundle,
    FragmentForm,
    FragmentRole,
    read_supplied,
    supplied_events,
)
from ter.domain.context_metrics import (
    ContextMeasures,
    CriticalEvidence,
    CriticalItem,
    measure_context,
)
from ter.domain.events import EventKind
from ter.domain.lean.analysis import explain
from ter.domain.stream import EventClass

PRICING = "src/app/domain/pricing.py"
MODEL = "src/app/domain/model.py"
CHECKOUT = "src/app/service/checkout.py"
WEB = "src/app/adapters/web.py"
SUMMARY = "src/app/reports/summary.py"
TEST_PRICING = "tests/test_pricing.py"
TEST_CHECKOUT = "tests/test_checkout.py"

RATES = Rates(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    return shop_repo(tmp_path / "shop")


def builder(root: Path, events: list[Event] | None = None) -> ContextBuilder:
    evidence = repository_evidence(root, "python-ast")
    return ContextBuilder(
        evidence, RegexTokenizer(), ground_session(events or [], evidence)
    )


def bundle(
    root: Path, prompt: str, budget: int = 8000, inherited: tuple[str, ...] = ()
) -> ContextBundle:
    return builder(root).build(
        prompt, session_id="s", budget=budget, inherited=inherited
    )


def by_source(b: ContextBundle) -> dict[str, tuple[FragmentRole, FragmentForm]]:
    return {f.source: (f.role, f.form) for f in b.fragments}


def big_pricing() -> str:
    body = "".join(f"    total = total + {i} * rate\n" for i in range(40))
    return SHOP[PRICING] + f"\n\ndef long_rule(total, rate):\n{body}    return total\n"


def book() -> InMemoryPriceBook:
    return InMemoryPriceBook([PriceEntry("model-x", date(2025, 1, 1), RATES)])


def turn(s: Script, text: str, **usage: int) -> Event:
    """A response carrying provider usage: one model turn."""
    event = s.say(text)
    priced = replace(event, usage=TokenUsage(model="model-x", **usage))
    s.events[-1] = priced
    return priced


# ---------------------------------------------------------------------------
# TER-CTX-001: selection, determinism, budget, sufficiency
# ---------------------------------------------------------------------------


class TestSelection:
    @pytest.mark.req("TER-CTX-001")
    def test_a_bundle_holds_the_change_surface_of_the_named_file(
        self, shop: Path
    ) -> None:
        b = bundle(shop, "Fix the rounding in pricing.py")
        assert b.basis == "named"
        assert b.seeds == (PRICING,)
        assert by_source(b) == {
            PRICING: (FragmentRole.SEED, FragmentForm.FILE),
            TEST_PRICING: (FragmentRole.SEED_TEST, FragmentForm.FILE),
            # Tiny files: whole text is smaller than their outline.
            CHECKOUT: (FragmentRole.NEIGHBOUR, FragmentForm.FILE),
            MODEL: (FragmentRole.NEIGHBOUR, FragmentForm.FILE),
            TEST_CHECKOUT: (FragmentRole.NEIGHBOUR_TEST, FragmentForm.FILE),
        }
        # Only the evidence selected: nothing outside the surface.
        assert not {WEB, SUMMARY, "tests/test_summary.py", "README.md"} & b.sources
        assert [f.role.rank for f in b.fragments] == sorted(
            f.role.rank for f in b.fragments
        )
        assert not b.insufficient

    @pytest.mark.req("TER-CTX-001")
    def test_every_fragment_has_an_id_source_reason_and_token_count(
        self, shop: Path
    ) -> None:
        b = bundle(shop, "Fix the rounding in pricing.py")
        tokens = RegexTokenizer()
        for f in b.fragments:
            assert f.id.startswith("ctx-") and len(f.id) == 20
            assert f.source in SHOP
            assert f.reason
            assert f.tokens == tokens.count(f.text) > 0
        reasons = {f.source: f.reason for f in b.fragments}
        assert reasons[PRICING] == "named by the prompt"
        assert reasons[TEST_PRICING] == f"test linked to seed {PRICING}"
        assert reasons[CHECKOUT] == f"imports seed {PRICING}"
        assert reasons[MODEL] == f"imported by seed {PRICING}"
        assert reasons[TEST_CHECKOUT] == f"test linked to {CHECKOUT}"
        assert SHOP[PRICING] == next(f.text for f in b.fragments if f.source == PRICING)

    @pytest.mark.req("TER-CTX-001")
    def test_a_large_neighbour_comes_as_its_outline(self, tmp_path: Path) -> None:
        shop = shop_repo(tmp_path / "big", {**SHOP, PRICING: big_pricing()})
        b = bundle(shop, "Fix checkout.py")
        assert by_source(b)[PRICING] == (FragmentRole.NEIGHBOUR, FragmentForm.OUTLINE)
        outline = next(f.text for f in b.fragments if f.source == PRICING)
        assert outline.splitlines() == [
            f"# outline of {PRICING} (python)",
            "import app.domain.model (order_total)  # line 1",
            "function price_with_tax  # lines 4-5",
            "function long_rule  # lines 8-49",
        ]

    @pytest.mark.req("TER-CTX-001")
    def test_a_distinctive_symbol_names_its_file(self, shop: Path) -> None:
        assert bundle(shop, "price_with_tax returns too much").seeds == (PRICING,)

    @pytest.mark.req("TER-CTX-001")
    def test_a_prompt_naming_nothing_selects_nothing_and_says_so(
        self, shop: Path
    ) -> None:
        b = bundle(shop, "Make it faster please")
        assert (b.basis, b.seeds, b.fragments) == ("none", (), ())
        assert b.insufficient
        inherited = bundle(shop, "now do it", inherited=(PRICING,))
        assert inherited.basis == "inherited"
        assert inherited.seeds == (PRICING,)
        assert (
            inherited.fragments[0].reason
            == "named by an earlier prompt this one continues"
        )


class TestDeterminism:
    @pytest.mark.req("TER-CTX-001")
    def test_the_same_inputs_give_byte_identical_bundles(
        self, shop: Path, tmp_path: Path
    ) -> None:
        first = bundle(shop, "Fix the rounding in pricing.py")
        again = bundle(shop, "Fix the rounding in pricing.py")
        elsewhere = tmp_path / "elsewhere" / "copy"
        shutil.copytree(shop, elsewhere)
        moved = bundle(elsewhere, "Fix the rounding in pricing.py")
        assert first.to_json() == again.to_json() == moved.to_json()
        assert first.render() == again.render() == moved.render()
        assert first.id == moved.id

    @pytest.mark.req("TER-CTX-001")
    def test_a_different_input_gives_a_different_bundle(self, shop: Path) -> None:
        a = bundle(shop, "Fix the rounding in pricing.py")
        b = bundle(shop, "Fix the rounding in pricing.py", budget=7999)
        c = bundle(shop, "Fix checkout.py")
        assert len({a.id, b.id, c.id}) == 3
        assert json.loads(a.to_json())["schema"] == "ter.context-bundle/1"


class TestBudget:
    @pytest.mark.req("TER-CTX-001")
    @pytest.mark.parametrize("budget", [0, 5, 20, 40, 60, 80, 120, 400])
    def test_a_bundle_never_exceeds_its_budget(self, shop: Path, budget: int) -> None:
        b = bundle(shop, "Fix the rounding in pricing.py", budget)
        assert b.tokens <= budget
        assert {f.source for f in b.fragments} | {o.source for o in b.omitted} == {
            PRICING,
            TEST_PRICING,
            CHECKOUT,
            MODEL,
            TEST_CHECKOUT,
        }

    @pytest.mark.req("TER-CTX-001")
    def test_boundary_a_seed_that_just_fits_is_whole_one_token_less_is_an_outline(
        self, tmp_path: Path
    ) -> None:
        text = big_pricing()
        shop = shop_repo(tmp_path / "big", {**SHOP, PRICING: text})
        whole = RegexTokenizer().count(text)
        fits = bundle(shop, "Fix pricing.py", whole)
        assert by_source(fits)[PRICING] == (FragmentRole.SEED, FragmentForm.FILE)
        assert fits.tokens == whole
        short = bundle(shop, "Fix pricing.py", whole - 1)
        assert by_source(short)[PRICING] == (FragmentRole.SEED, FragmentForm.OUTLINE)
        assert not short.insufficient

    @pytest.mark.req("TER-CTX-001")
    def test_a_seed_is_never_dropped_for_lower_ranked_evidence(
        self, shop: Path
    ) -> None:
        # A budget that holds the seed exactly, or several outlines instead:
        # the seed takes it and the rest of the surface is omitted.
        whole = RegexTokenizer().count(SHOP[PRICING])
        b = bundle(shop, "Fix pricing.py", whole)
        assert b.sources == {PRICING}
        assert {o.source for o in b.omitted} == {
            TEST_PRICING,
            CHECKOUT,
            MODEL,
            TEST_CHECKOUT,
        }
        # Too small for the seed in any form: the bundle says it is
        # insufficient rather than passing a smaller bundle off as complete
        # (P159).
        smallest = bundle(shop, "Fix pricing.py", 0).omitted[0].tokens
        assert 0 < smallest <= whole
        small = bundle(shop, "Fix pricing.py", smallest - 1)
        assert small.insufficient
        assert small.omitted[0].source == PRICING
        assert small.omitted[0].role is FragmentRole.SEED
        assert "Insufficient" in small.render()
        empty = bundle(shop, "Fix pricing.py", 0)
        assert empty.fragments == () and empty.insufficient


# ---------------------------------------------------------------------------
# TER-EVD-004: context.supplied events
# ---------------------------------------------------------------------------


def pricing_session() -> Script:
    s = Script()
    s.prompt("Fix the rounding in pricing.py")
    return s


class TestSupplied:
    @pytest.mark.req("TER-EVD-004")
    def test_supplying_a_bundle_appends_one_event_per_fragment(
        self, shop: Path
    ) -> None:
        s = pricing_session()
        log = InMemoryEventLog()
        for e in s.events:
            log.append(e)
        clock = FixedClock(datetime(2026, 1, 2, tzinfo=UTC))
        supply = SupplyContext(
            repository_evidence(shop, "python-ast"), RegexTokenizer(), log, clock
        )
        out = supply(session_id="s")
        logged = log.events("s")
        assert logged[: len(s.events)] == tuple(s.events)
        appended = logged[len(s.events) :]
        assert appended == out.events
        assert len(appended) == len(out.bundle.fragments) == 5
        for event, fragment in zip(appended, out.bundle.fragments, strict=True):
            assert event.kind is EventKind.CONTEXT_SUPPLIED
            assert event.parent_id == s.events[0].id
            assert event.timestamp == clock.now()
            record = read_supplied(event)
            assert record is not None
            assert (record.fragment, record.source, record.reason) == (
                fragment.id,
                fragment.source,
                fragment.reason,
            )
            assert record.bundle == out.bundle.id
        assert [e.sequence for e in appended] == list(range(1, 6))

    @pytest.mark.req("TER-EVD-004")
    def test_supplying_the_same_bundle_again_yields_the_same_event_ids(
        self, shop: Path
    ) -> None:
        log = InMemoryEventLog()
        supply = SupplyContext(
            repository_evidence(shop, "python-ast"), RegexTokenizer(), log
        )
        first = supply(session_id="s", prompt_text="Fix pricing.py")
        second = supply(session_id="s", prompt_text="Fix pricing.py")
        assert [e.id for e in first.events] == [e.id for e in second.events]

    @pytest.mark.req("TER-EVD-004")
    def test_the_event_survives_the_event_log_and_is_lifecycle(
        self, shop: Path
    ) -> None:
        b = bundle(shop, "Fix pricing.py")
        events = supplied_events(b)
        for e in events:
            assert event_from_record(json.loads(json.dumps(event_to_record(e)))) == e
        assert EventClass.of(EventKind.CONTEXT_SUPPLIED) is EventClass.LIFECYCLE
        assert read_supplied(pricing_session().events[0]) is None
        # Recording a supply changes no Lean measure: it is not a step.
        s = pricing_session()
        s.read(at(PRICING), SHOP[PRICING])
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        plain = explain(s.events, RegexTokenizer())
        mixed = explain([*s.events[:1], *events, *s.events[1:]], RegexTokenizer())
        assert len(mixed.steps) == len(plain.steps)
        assert [f.detector for f in mixed.findings] == [
            f.detector for f in plain.findings
        ]

    @pytest.mark.req("TER-EVD-004")
    def test_a_session_without_a_prompt_needs_the_prompt_text(self, shop: Path) -> None:
        supply = SupplyContext(
            repository_evidence(shop, "python-ast"), RegexTokenizer()
        )
        with pytest.raises(ValueError, match="no prompt"):
            supply(session_id="s", events=())


# ---------------------------------------------------------------------------
# TER-CTX-003: precision and recall
# ---------------------------------------------------------------------------


def measure(
    shop: Path,
    s: Script,
    critical: CriticalEvidence | None = None,
    prices: InMemoryPriceBook | None = None,
) -> ContextMeasures:
    use_case = MeasureContext(
        repository_evidence(shop, "python-ast"), RegexTokenizer(), prices
    )
    return use_case(s.events, critical=critical)


class TestPrecisionRecall:
    @pytest.mark.req("TER-CTX-003")
    def test_precision_counts_fragments_the_session_used(self, shop: Path) -> None:
        s = pricing_session()
        s.read(at(PRICING), SHOP[PRICING])
        edit, _ = s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        run, _ = s.bash("pytest tests/test_pricing.py -q", PASS)
        m = measure(shop, s)
        assert m.simulated
        (b,) = m.bundles
        used = {f.source: f.used_by for f in b.fragments}
        assert used[PRICING] == (edit.id,)
        assert used[TEST_PRICING] == (run.id,)
        assert used[MODEL] == used[CHECKOUT] == used[TEST_CHECKOUT] == ()
        assert b.precision == pytest.approx(2 / 5)
        # Needed: the file edited and the test run; the bundle held both.
        assert b.needed == (PRICING, TEST_PRICING)
        assert b.recall == 1.0
        assert b.missing == ()

    @pytest.mark.req("TER-CTX-003")
    def test_a_symbol_named_in_an_edit_uses_the_file_that_defines_it(
        self, shop: Path
    ) -> None:
        s = pricing_session()
        edit, _ = s.edit(
            at(PRICING), "order_total(items) * 1.2", "order_total(items) * 12 / 10"
        )
        (b,) = measure(shop, s).bundles
        used = {f.source: f.used_by for f in b.fragments}
        assert used[MODEL] == (edit.id,)  # order_total is defined in model.py

    @pytest.mark.req("TER-CTX-003")
    def test_reading_a_file_again_is_not_use(self, shop: Path) -> None:
        s = pricing_session()
        s.read(at(CHECKOUT), SHOP[CHECKOUT])
        (b,) = measure(shop, s).bundles
        assert b.precision == 0.0
        assert b.recall is None  # nothing was needed: no edit, no test

    @pytest.mark.req("TER-CTX-003")
    def test_recall_falls_when_the_session_edits_a_file_the_bundle_lacked(
        self, shop: Path
    ) -> None:
        s = pricing_session()
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        read, _ = s.read(at(WEB), SHOP[WEB])
        edit, _ = s.edit(at(WEB), "checkout(request)", "checkout(request.items)")
        (b,) = measure(shop, s).bundles
        assert b.needed == (WEB, PRICING)
        assert b.held == (PRICING,)
        assert b.recall == 0.5
        (miss,) = b.missing
        assert (miss.path, miss.depended_by, miss.read_by) == (
            WEB,
            (edit.id,),
            (read.id,),
        )

    @pytest.mark.req("TER-CTX-003")
    def test_each_bundle_is_measured_on_its_own_task(self, shop: Path) -> None:
        s = pricing_session()
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        s.prompt("Now count rows in summary.py")
        s.edit(at(SUMMARY), "len(rows)", "len(list(rows))")
        first, second = measure(shop, s).bundles
        assert first.needed == (PRICING,) and first.recall == 1.0
        assert second.needed == (SUMMARY,) and second.recall == 1.0
        assert {f.source for f in second.fragments} == {
            SUMMARY,
            "tests/test_summary.py",
        }

    @pytest.mark.req("TER-CTX-003")
    def test_supplied_bundles_are_measured_as_supplied(self, shop: Path) -> None:
        s = pricing_session()
        log = InMemoryEventLog()
        for e in s.events:
            log.append(e)
        SupplyContext(repository_evidence(shop, "python-ast"), RegexTokenizer(), log)(
            session_id="s", prompt_text="Fix checkout.py"
        )
        events = list(log.events("s"))
        s.events[:] = events
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        m = measure(shop, s)
        assert not m.simulated
        (b,) = m.bundles
        assert {f.source for f in b.fragments} >= {CHECKOUT, PRICING}
        # pricing.py was supplied as a neighbour of checkout.py and edited.
        assert next(f for f in b.fragments if f.source == PRICING).used

    @pytest.mark.req("TER-CTX-003")
    def test_boundary_a_prompt_before_any_tool_request_stays_in_the_window(
        self, shop: Path
    ) -> None:
        # A bundle built ahead of its prompt (``--prompt``) keeps that prompt's
        # task in its window; the prompt after a tool request ends it.
        b = bundle(shop, "Fix pricing.py")
        s = Script()
        s.events.extend(supplied_events(b))
        s.prompt("Fix pricing.py")
        edit, _ = s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        s.prompt("Next: summary.py")
        s.edit(at(SUMMARY), "len(rows)", "len(list(rows))")
        g = ground_session(s.events, repository_evidence(shop, "python-ast"))
        (m,) = measure_context(s.events, g).bundles
        assert m.needed == (PRICING,)
        assert next(f for f in m.fragments if f.source == PRICING).used_by == (edit.id,)


# ---------------------------------------------------------------------------
# TER-EVD-005: critical recall
# ---------------------------------------------------------------------------


class TestCriticalRecall:
    @pytest.mark.req("TER-EVD-005")
    def test_items_in_context_before_their_first_dependent_edit(
        self, shop: Path
    ) -> None:
        critical = CriticalEvidence(
            "s",
            (
                CriticalItem(PRICING),
                CriticalItem(MODEL, "order_total"),
                CriticalItem(WEB),
                CriticalItem(SUMMARY),
            ),
            "list.json",
        )
        s = pricing_session()
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")  # depends on pricing and model
        s.read(at(WEB), SHOP[WEB])
        s.edit(at(WEB), "checkout(request)", "checkout(request.items)")
        m = measure(shop, s, critical)
        assert m.critical is not None
        results = {r.item.path: r for r in m.critical.items}
        assert results[PRICING].in_context and results[MODEL].in_context
        # web.py was read by the agent before its edit: in context.
        assert results[WEB].in_context
        assert results[WEB].in_context_by == s.events[-4].id
        assert results[SUMMARY].first_dependent_edit is None
        assert m.critical.recall == 1.0
        assert len(m.critical.judged) == 3
        # Per bundle the needed items are the listed ones: web.py was not held.
        (b,) = m.bundles
        assert b.needed_basis == "critical"
        assert b.needed == (WEB, MODEL, PRICING)
        assert [x.path for x in b.missing] == [WEB]

    @pytest.mark.req("TER-EVD-005")
    def test_boundary_a_read_after_the_dependent_edit_is_too_late(
        self, shop: Path
    ) -> None:
        critical = CriticalEvidence("s", (CriticalItem(WEB),))
        s = Script()
        s.prompt("tidy the handler")
        early, _ = s.edit(at(WEB), "checkout(request)", "checkout(request.items)")
        s.read(at(WEB), SHOP[WEB])
        g = ground_session(s.events, repository_evidence(shop, "python-ast"))
        m = measure_context(s.events, g, critical=critical)
        assert m.critical is not None
        (r,) = m.critical.items
        assert r.first_dependent_edit == early.id
        assert not r.in_context
        assert m.critical.recall == 0.0

    @pytest.mark.req("TER-EVD-005")
    def test_an_edit_naming_the_listed_symbol_depends_on_it(self, shop: Path) -> None:
        critical = CriticalEvidence("s", (CriticalItem(MODEL, "order_total"),))
        s = Script()
        s.prompt("tidy")
        edit, _ = s.edit(at(SUMMARY), "len(rows)", "order_total(rows)")
        g = ground_session(s.events, repository_evidence(shop, "python-ast"))
        m = measure_context(s.events, g, critical=critical)
        assert m.critical is not None
        assert m.critical.items[0].first_dependent_edit == edit.id
        assert m.critical.recall == 0.0

    @pytest.mark.req("TER-EVD-005")
    def test_no_dependent_edit_gives_no_recall(self, shop: Path) -> None:
        critical = CriticalEvidence("s", (CriticalItem(SUMMARY),))
        s = pricing_session()
        g = ground_session(s.events, repository_evidence(shop, "python-ast"))
        m = measure_context(s.events, g, critical=critical)
        assert m.critical is not None and m.critical.recall is None


class TestCriticalFile:
    @pytest.mark.req("TER-EVD-005")
    def test_json_and_csv_lists_read_alike(self, tmp_path: Path) -> None:
        j = tmp_path / "critical.json"
        j.write_text(
            json.dumps(
                {
                    "schema": "ter.critical-evidence/1",
                    "sessions": {
                        "s": [f"./{PRICING}", {"path": MODEL, "symbol": "order_total"}],
                        "other": [WEB],
                    },
                }
            ),
            encoding="utf-8",
        )
        c = tmp_path / "critical.csv"
        c.write_text(
            "session_id,path,symbol\n"
            f"s,{MODEL},order_total\n"
            f"s,{PRICING.replace('/', chr(92))},\n"
            f"other,{WEB},\n",
            encoding="utf-8",
        )
        expected = (CriticalItem(MODEL, "order_total"), CriticalItem(PRICING))
        for f in (j, c):
            read = read_critical_evidence(f, "s")
            assert read is not None and read.items == expected
            assert read.source == f.name
            assert read_critical_evidence(f, "missing") is None

    @pytest.mark.req("TER-EVD-005")
    @pytest.mark.parametrize(
        ("name", "text"),
        [
            ("a.json", "{oops"),
            ("a.json", '{"schema": "other", "sessions": {}}'),
            ("a.json", '{"schema": "ter.critical-evidence/1", "sessions": {"s": 3}}'),
            (
                "a.json",
                '{"schema": "ter.critical-evidence/1", "sessions": {"s": ["/abs"]}}',
            ),
            ("a.csv", "path\nsrc/a.py\n"),
            ("a.csv", "session_id,path\n,src/a.py\n"),
        ],
    )
    def test_a_bad_list_is_refused(self, tmp_path: Path, name: str, text: str) -> None:
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        with pytest.raises(CriticalEvidenceError):
            read_critical_evidence(f, "s")


# ---------------------------------------------------------------------------
# TER-CTX-004: unused context as inventory, missing context as defect risk
# ---------------------------------------------------------------------------


class TestInventoryAndRisk:
    @pytest.mark.req("TER-CTX-004")
    def test_unused_fragments_are_priced_as_carrying_cost(self, shop: Path) -> None:
        s = pricing_session()
        turn(s, "editing", input_tokens=10)
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        turn(s, "done", input_tokens=10)
        m = measure(shop, s, prices=book())
        (b,) = m.bundles
        unused = sum(f.tokens for f in b.fragments if not f.used)
        assert b.unused_tokens == unused > 0
        assert b.carried_turns == 2
        # No caching shown: ingested at the input rate, carried at it once more.
        assert b.unused_usd == pytest.approx(unused * 2 * RATES.input / 1_000_000)
        assert m.unused_usd == b.unused_usd
        assert m.price_book == "in-memory"

    @pytest.mark.req("TER-CTX-004")
    def test_with_caching_the_carry_is_at_cache_rates(self, shop: Path) -> None:
        s = pricing_session()
        turn(s, "a", input_tokens=1, cache_creation_tokens=5)
        turn(s, "b", input_tokens=1, cache_read_tokens=5)
        turn(s, "c", input_tokens=1, cache_read_tokens=5)
        (b,) = measure(shop, s, prices=book()).bundles
        expected = b.unused_tokens * (RATES.cache_write + 2 * RATES.cache_read)
        assert b.unused_usd == pytest.approx(expected / 1_000_000)

    @pytest.mark.req("TER-CTX-004")
    def test_without_prices_there_are_tokens_but_no_cost(self, shop: Path) -> None:
        s = pricing_session()
        turn(s, "a", input_tokens=1)
        m = measure(shop, s)
        assert m.bundles[0].unused_tokens > 0
        assert m.bundles[0].unused_usd is None and m.unused_usd is None

    @pytest.mark.req("TER-CTX-004")
    def test_missing_context_names_the_edits_that_depended_on_it(
        self, shop: Path
    ) -> None:
        s = pricing_session()
        first, _ = s.edit(at(WEB), "checkout(request)", "checkout(request.items)")
        again, _ = s.edit(at(WEB), "request.items", "list(request.items)")
        (b,) = measure(shop, s).bundles
        (miss,) = b.missing
        assert miss.path == WEB
        assert miss.depended_by == (first.id, again.id)
        assert miss.read_by == ()  # never in context: the edit ran blind
        assert b.as_dict()["missing"] == [miss.as_dict()]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class TestCli:
    @pytest.mark.req("TER-EVD-004")
    @pytest.mark.req("TER-CTX-001")
    def test_bundle_prints_the_bundle_and_records_its_supply(
        self, shop: Path, tmp_path: Path
    ) -> None:
        logdir = tmp_path / "log"
        log = JsonlEventLog(logdir)
        for e in pricing_session().events:
            log.append(e)
        out_file = tmp_path / "bundle.json"
        argv = [
            "context", "bundle", "--session", "s", "--repo", str(shop),
            "--repo-engine", "python-ast", "--event-log", str(logdir),
            "--out", str(out_file),
        ]  # fmt: skip
        code, out, err = run(argv)
        assert code == 0, err
        assert out.startswith("# Context bundle bnd-")
        assert f"## {PRICING} (file," in out
        written = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(written["fragments"]) == 5
        supplied = [read_supplied(e) for e in log.events("s")[1:]]
        assert [x.fragment for x in supplied if x] == [
            f["id"] for f in written["fragments"]
        ]
        assert "Recorded 5 context.supplied event(s)" in err
        # Byte-identical when run again (the events are redeliveries).
        code, again, _ = run(argv)
        assert again == out

    @pytest.mark.req("TER-EVD-004")
    def test_no_record_appends_nothing(self, shop: Path, tmp_path: Path) -> None:
        logdir = tmp_path / "log"
        code, out, err = run(
            [
                "context", "bundle", "--session", "x", "--prompt", "Fix pricing.py",
                "--repo", str(shop), "--event-log", str(logdir), "--no-record",
                "--json",
            ]
        )  # fmt: skip
        assert code == 0, err
        assert json.loads(out)["seeds"] == [PRICING]
        assert not logdir.exists()

    @pytest.mark.req("TER-CTX-003")
    @pytest.mark.req("TER-CTX-004")
    @pytest.mark.req("TER-EVD-005")
    def test_report_prints_the_measures(self, shop: Path, tmp_path: Path) -> None:
        logdir = tmp_path / "log"
        log = JsonlEventLog(logdir)
        s = pricing_session()
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        s.edit(at(WEB), "checkout(request)", "checkout(request.items)")
        for e in s.events:
            log.append(e)
        critical = tmp_path / "critical.csv"
        critical.write_text(f"session_id,path,symbol\ns,{WEB},\n", encoding="utf-8")
        base = [
            "context", "report", "--event-log", str(logdir), "--session", "s",
            "--repo", str(shop), "--critical", str(critical),
        ]  # fmt: skip
        code, out, err = run(base)
        assert code == 0, err
        assert "rebuilt for each prompt" in out
        assert "precision 20% (1/5)" in out
        assert f"missing {WEB}" in out
        assert "critical recall  0% (0/1" in out
        code, out, err = run([*base, "--json"])
        data = json.loads(out)
        assert data["schema"] == "ter.context-measures/1"
        assert data["bundles"][0]["inventory"]["unused_tokens"] > 0
        assert data["critical"]["recall"] == 0.0

    def test_bad_input_is_refused(self, shop: Path, tmp_path: Path) -> None:
        code, _, err = run(["context", "bundle", "--repo", str(shop)])
        assert code == 2 and "--session" in err
        code, _, err = run(["context", "report", "--repo", str(tmp_path / "no")])
        assert code == 2 and "No such repository" in err
        code, _, err = run(
            ["context", "bundle", "--session", "s", "--repo", str(shop), "--no-record"]
        )
        assert code == 2 and "no prompt" in err
