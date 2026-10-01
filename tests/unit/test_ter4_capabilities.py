"""The capability registry: ports' adapters by entry point (TER-ARC-004..006).

Entry points are faked, so these tests do not depend on what is installed;
one test reads the real installed metadata and ``pyproject.toml``.
"""

from __future__ import annotations

import io
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ter.adapters.driving.cli import CliServices, main
from ter.bootstrap import make_tokenizer
from ter.bootstrap.capabilities import (
    BUILTIN_CAPABILITIES,
    CapabilityRegistry,
    EntryLike,
    default_registry,
)
from ter.domain.capabilities import (
    CAPABILITY_GROUP,
    Capability,
    CapabilityError,
    CapabilityProblem,
    UnknownCapabilityError,
    parse_capability_key,
)
from ter.ports import DRIVEN_PORTS, Tokenizer

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class WordTokenizer:
    name = "words"
    exact = False

    def count(self, text: str) -> int:
        return len(text.split())


class NoCount:
    name = "broken"
    exact = False


class NoName:
    exact = False

    def count(self, text: str) -> int:
        return 0


@dataclass
class FakeEntry:
    """Stands in for ``importlib.metadata.EntryPoint``; counts loads."""

    name: str
    value: str
    target: Callable[[], Any]
    loads: list[int] = field(default_factory=list)

    def load(self) -> Any:
        self.loads.append(1)
        return self.target()


def _entry(name: str, obj: object, value: str = "plugin.module:Thing") -> FakeEntry:
    return FakeEntry(name, value, lambda: obj)


def _broken(name: str) -> FakeEntry:
    def fail() -> Any:
        raise ImportError("No module named 'plugin_dependency'")

    return FakeEntry(name, "plugin.broken:Thing", fail)


def _registry(*entries: EntryLike) -> CapabilityRegistry:
    return CapabilityRegistry(discover=lambda: entries)


# --- discovery ----------------------------------------------------------------


@pytest.mark.req("TER-ARC-004")
def test_entry_points_register_adapters_by_port_and_name() -> None:
    reg = _registry(_entry("Tokenizer.words", WordTokenizer))
    assert "words" in reg.names("Tokenizer")
    cap = next(c for c in reg.capabilities("Tokenizer") if c.name == "words")
    assert cap == Capability("Tokenizer", "words", "plugin.module:Thing", "entry point")
    tok = reg.create("Tokenizer", "words")
    assert isinstance(tok, Tokenizer)
    assert tok.count("a b c") == 3


@pytest.mark.req("TER-ARC-004")
def test_built_ins_are_registered_alongside_entry_points() -> None:
    reg = _registry(_entry("Tokenizer.words", WordTokenizer))
    keys = {c.key for c in reg.capabilities()}
    assert set(BUILTIN_CAPABILITIES) | {"Tokenizer.words"} == keys
    assert all(
        c.origin == "built-in"
        for c in reg.capabilities()
        if c.key in BUILTIN_CAPABILITIES
    )


@pytest.mark.req("TER-ARC-004")
def test_pyproject_declares_exactly_the_built_in_capabilities() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    declared = project["entry-points"][CAPABILITY_GROUP]
    assert declared == BUILTIN_CAPABILITIES
    for key in declared:
        port, _ = parse_capability_key(key)
        assert port in DRIVEN_PORTS


@pytest.mark.req("TER-ARC-004")
def test_installed_registry_loads_every_built_in_without_problems() -> None:
    # Reads the real installed metadata. An entry identical to a built-in is
    # the same capability, so an installed TER adds no duplicates or problems.
    reg = CapabilityRegistry()
    assert reg.check() == ()
    assert set(BUILTIN_CAPABILITIES) <= {c.key for c in reg.capabilities()}


def test_capability_keys_name_a_port_and_an_adapter() -> None:
    assert parse_capability_key("SessionSource.claude-code") == (
        "SessionSource",
        "claude-code",
    )
    for bad in ("tokenizer.regex", "Tokenizer", "Tokenizer.", "Tokenizer.Regex", ".x"):
        with pytest.raises(CapabilityError, match="<Port>.<adapter>"):
            parse_capability_key(bad)


# --- laziness -----------------------------------------------------------------


@pytest.mark.req("TER-ARC-006")
def test_capabilities_load_only_when_asked_for_and_only_once() -> None:
    entry = _entry("Tokenizer.words", WordTokenizer)
    reg = _registry(entry)
    assert reg.names("Tokenizer") and entry.loads == []
    reg.create("Tokenizer", "words")
    reg.create("Tokenizer", "words")
    assert entry.loads == [1]


@pytest.mark.req("TER-ARC-006")
def test_built_ins_resolve_without_scanning_installed_packages() -> None:
    scans: list[int] = []

    def discover() -> Iterable[EntryLike]:
        scans.append(1)
        return ()

    reg = CapabilityRegistry(discover=discover)
    assert isinstance(reg.create("Tokenizer", "regex"), Tokenizer)
    assert scans == []  # a hook pays nothing for discovery
    with pytest.raises(
        UnknownCapabilityError, match="no Tokenizer capability named 'nope'"
    ):
        reg.create("Tokenizer", "nope")
    assert scans == [1]


# --- rejection and problems ---------------------------------------------------


@pytest.mark.req("TER-ARC-005")
def test_a_class_missing_a_port_method_is_rejected_with_a_clear_error() -> None:
    reg = _registry(_entry("Tokenizer.broken", NoCount))
    with pytest.raises(CapabilityError) as raised:
        reg.factory("Tokenizer", "broken")
    message = str(raised.value)
    assert "Tokenizer.broken" in message and "plugin.module:Thing" in message
    assert "does not satisfy the Tokenizer port: missing count" in message
    assert [p.key for p in reg.problems] == ["Tokenizer.broken"]


@pytest.mark.req("TER-ARC-005")
def test_an_instance_missing_a_port_attribute_is_rejected() -> None:
    reg = _registry(_entry("Tokenizer.anon", NoName))
    with pytest.raises(CapabilityError, match="missing name"):
        reg.create("Tokenizer", "anon")


@pytest.mark.req("TER-ARC-005")
def test_a_non_callable_object_is_rejected() -> None:
    reg = _registry(_entry("Tokenizer.value", 42))
    with pytest.raises(CapabilityError, match="not a class or factory"):
        reg.factory("Tokenizer", "value")


@pytest.mark.req("TER-ARC-005")
def test_a_broken_plugin_is_reported_and_the_rest_keep_working() -> None:
    reg = _registry(
        _broken("Tokenizer.fragile"), _entry("Tokenizer.words", WordTokenizer)
    )
    problems = reg.check()  # never raises
    assert [(p.key, p.target) for p in problems] == [
        ("Tokenizer.fragile", "plugin.broken:Thing")
    ]
    assert "ImportError: No module named 'plugin_dependency'" in problems[0].reason
    assert isinstance(reg.create("Tokenizer", "words"), Tokenizer)
    assert isinstance(reg.create("Tokenizer", "regex"), Tokenizer)
    with pytest.raises(CapabilityError, match="failed to load"):
        reg.create("Tokenizer", "fragile")


@pytest.mark.req("TER-ARC-005")
def test_malformed_and_unknown_port_entries_are_reported_not_raised() -> None:
    reg = _registry(
        _entry("tokenizer", WordTokenizer),
        _entry("Teleporter.beam", WordTokenizer),
        _entry("Tokenizer.words", WordTokenizer),
    )
    reasons = {p.key: p.reason for p in reg.problems}
    assert "<Port>.<adapter>" in reasons["tokenizer"]
    assert "unknown port 'Teleporter'" in reasons["Teleporter.beam"]
    assert reg.names("Tokenizer") == ("regex", "tiktoken", "words")


@pytest.mark.req("TER-ARC-005")
def test_failing_discovery_leaves_the_built_ins_working() -> None:
    def explode() -> Iterable[EntryLike]:
        raise OSError("corrupt package metadata")

    reg = CapabilityRegistry(discover=explode)
    assert [p.reason for p in reg.problems] == [
        "entry point discovery failed: corrupt package metadata"
    ]
    assert isinstance(reg.create("Tokenizer", "regex"), Tokenizer)


@pytest.mark.req("TER-ARC-005")
def test_an_entry_point_cannot_replace_a_built_in() -> None:
    same = _entry(
        "Tokenizer.regex", WordTokenizer, BUILTIN_CAPABILITIES["Tokenizer.regex"]
    )
    hijack = _entry("Tokenizer.regex", WordTokenizer, "evil.module:Tokenizer")
    reg = _registry(same, hijack)
    assert [p.target for p in reg.problems] == ["evil.module:Tokenizer"]
    assert "already registered by built-in" in reg.problems[0].reason
    tok = reg.create("Tokenizer", "regex")
    assert isinstance(tok, Tokenizer) and tok.name == "regex-v1"
    assert same.loads == hijack.loads == []


# --- composition root and CLI -------------------------------------------------


def test_make_tokenizer_goes_through_the_registry() -> None:
    assert make_tokenizer("regex").name == "regex-v1"
    with pytest.raises(ValueError, match="Unknown tokenizer"):
        make_tokenizer("nope")
    assert default_registry() is default_registry()


def _cli(
    found: tuple[Capability, ...], problems: tuple[CapabilityProblem, ...]
) -> tuple[int, str]:
    services = CliServices(
        analyse_transcript=lambda *a: pytest.fail("unused"),
        log_sessions=lambda *a: pytest.fail("unused"),
        analyse_log=lambda *a: pytest.fail("unused"),
        hook_ingest=lambda *a: pytest.fail("unused"),
        default_log_dir=Path("."),
        capabilities=lambda: (found, problems),
    )
    out = io.StringIO()
    code = main(["capabilities"], services, stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


@pytest.mark.req("TER-ARC-005")
def test_capabilities_command_lists_adapters_and_reports_broken_ones() -> None:
    reg = _registry(_broken("Tokenizer.fragile"))
    problems = reg.check()
    code, text = _cli(reg.capabilities(), problems)
    assert code == 1
    assert "Tokenizer      regex" in text
    assert "! Tokenizer.fragile (plugin.broken:Thing): failed to load" in text
    assert "fragile  " not in text  # broken ones are not listed as usable
    code, _ = _cli(CapabilityRegistry(discover=None).capabilities(), ())
    assert code == 0


@pytest.mark.req("TER-ARC-005")
def test_capabilities_command_reports_a_class_missing_a_port_attribute() -> None:
    reg = _registry(_entry("Tokenizer.anon", NoName))
    problems = reg.check()  # static: the class is never instantiated
    assert [(p.key, p.reason) for p in problems] == [
        ("Tokenizer.anon", "does not satisfy the Tokenizer port: missing name")
    ]
    code, text = _cli(reg.capabilities(), problems)
    assert code == 1
    assert "! Tokenizer.anon (plugin.module:Thing): does not satisfy" in text
    assert "anon  " not in text


@pytest.mark.req("TER-ARC-005")
def test_check_accepts_attributes_set_in_init() -> None:
    class InitName:
        exact = False

        def __init__(self) -> None:
            self.name = "init"

        def count(self, text: str) -> int:
            return 0

    reg = _registry(_entry("Tokenizer.init", InitName))
    assert reg.check() == ()
    assert CapabilityRegistry(discover=None).check() == ()  # every built-in


@pytest.mark.req("TER-ARC-005")
def test_capabilities_command_keeps_a_built_in_a_plugin_tried_to_hijack() -> None:
    hijack = _entry("Tokenizer.regex", WordTokenizer, "evil.module:Tokenizer")
    reg = _registry(hijack)
    problems = reg.check()
    assert [(p.key, p.target) for p in problems] == [
        ("Tokenizer.regex", "evil.module:Tokenizer")
    ]
    code, text = _cli(reg.capabilities(), problems)
    assert code == 1
    usable = text.split("\n  !")[0]
    assert "8 usable, 1 problem(s)" in usable  # every built-in still counts
    assert any(
        line.split()[:3]
        == ["Tokenizer", "regex", "ter.adapters.driven.tokenizers:RegexTokenizer"]
        for line in usable.splitlines()
    )
    assert "evil.module" not in usable
    assert (
        "! Tokenizer.regex (evil.module:Tokenizer): Tokenizer.regex is already" in text
    )


@pytest.mark.req("TER-ARC-004")
def test_cli_tokenizer_accepts_a_registered_external_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ter import bootstrap
    from ter.bootstrap import capabilities as caps

    reg = _registry(_entry("Tokenizer.words", WordTokenizer))
    monkeypatch.setattr(caps, "default_registry", lambda: reg)
    monkeypatch.setattr(bootstrap, "default_registry", lambda: reg)
    session = Path(__file__).resolve().parents[1] / "golden" / "sessions"
    path = next(iter(sorted(session.glob("*.jsonl"))))
    for command in (["observe"], ["explain"], ["a3", "--ter", "off"]):
        out, err = io.StringIO(), io.StringIO()
        argv = [*command, str(path), "--tokenizer", "words"]
        code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
        assert code == 0, err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    argv = ["observe", str(path), "--json", "--tokenizer", "words"]
    code = main(argv, bootstrap.cli_services(), stdout=out, stderr=err)
    assert code == 0 and '"words"' in out.getvalue()
    err = io.StringIO()
    argv = ["observe", str(path), "--tokenizer", "nope"]
    code = main(argv, bootstrap.cli_services(), stdout=io.StringIO(), stderr=err)
    assert code == 2
    assert err.getvalue() == (
        "Unknown tokenizer 'nope' (available: regex, tiktoken, words)\n"
    )
