"""Contract suite for the ``RoutingProfiles`` port (TER-RTE-001).

The shipped JSON profiles and the in-memory fake run the same assertions, so
the fake cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from ter.adapters.driven.in_memory import InMemoryRoutingProfiles
from ter.adapters.driven.pricing import JsonPriceBook
from ter.adapters.driven.routing_profiles import (
    JsonRoutingProfiles,
    default_routing_profiles,
)
from ter.domain.routing import RoutingProfileError, TaskKind
from ter.ports import RoutingProfiles

pytestmark = pytest.mark.req("TER-RTE-001")

Factory = Callable[[], RoutingProfiles]


def _json() -> RoutingProfiles:
    return JsonRoutingProfiles()


def _memory() -> RoutingProfiles:
    real = JsonRoutingProfiles()
    return InMemoryRoutingProfiles(
        (real.profile(n) for n in real.names()), default=real.default()
    )


@pytest.fixture(params=[_json, _memory], ids=["json", "in-memory"])
def profiles(request: pytest.FixtureRequest) -> RoutingProfiles:
    factory: Factory = request.param
    return factory()


def test_satisfies_the_port_protocol(profiles: RoutingProfiles) -> None:
    assert isinstance(profiles, RoutingProfiles)
    assert profiles.name
    assert profiles.names() == tuple(sorted(profiles.names()))
    assert profiles.default() in profiles.names()


def test_profiles_are_deterministic(profiles: RoutingProfiles) -> None:
    for name in profiles.names():
        assert profiles.profile(name) == profiles.profile(name)
        assert profiles.profile(name).name == name


def test_every_named_role_is_defined(profiles: RoutingProfiles) -> None:
    for name in profiles.names():
        p = profiles.profile(name)
        assert set(p.task_roles) == set(TaskKind)
        for role in (*p.task_roles.values(), *p.escalation, *p.escalation.values()):
            assert p.binding(role).model and p.binding(role).provider


def test_unknown_profile_raises(profiles: RoutingProfiles) -> None:
    with pytest.raises(RoutingProfileError):
        profiles.profile("no-such-profile")


# --- the JSON adapter --------------------------------------------------------


def test_the_shipped_profiles_include_the_four_execution_profiles() -> None:
    # P145: local-fast, local-code, frontier-standard and frontier-deep ship
    # beside the default.
    assert default_routing_profiles().default() == "default"
    assert set(default_routing_profiles().names()) == {
        "default",
        "local-fast",
        "local-code",
        "frontier-standard",
        "frontier-deep",
    }


def test_every_hosted_role_is_priced() -> None:
    # Hosted model ids are price book entries, so the cost of a role (and
    # of an escalation) can be read from data (ADR 0003). Local models have
    # no price.
    book = JsonPriceBook()
    profiles = default_routing_profiles()
    for name in profiles.names():
        for binding in profiles.profile(name).roles.values():
            if binding.provider != "ollama":
                book.rate(binding.model)


def _write(directory: Path, name: str, document: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(document), encoding="utf-8")


def _document(**changes: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema": "ter.routing_profile/1",
        "name": "custom",
        "source": "test",
        "roles": {
            "a": {"provider": "p", "model": "m1"},
            "b": {"provider": "p", "model": "m2"},
        },
        "task_roles": {"read_only": "a", "validate": "a", "change": "a"},
        "escalation": {"a": "b"},
        "escalate_on": ["rework_cycle"],
    }
    doc.update(changes)
    return doc


def test_a_directory_of_profiles_is_read(tmp_path: Path) -> None:
    _write(tmp_path, "custom.json", _document())
    profiles = JsonRoutingProfiles(tmp_path)
    assert profiles.names() == ("custom",) and profiles.default() == "custom"
    assert profiles.profile("custom").escalates_to("a") == "b"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "x"}, "schema"),
        ({"name": ""}, "name"),
        ({"source": ""}, "source"),
        ({"roles": {}}, "roles"),
        ({"roles": {"a": {"provider": "p"}}}, "model"),
        (
            {"task_roles": {"read_only": "a", "validate": "a", "change": "zz"}},
            "undefined role",
        ),
        (
            {"task_roles": {"read_only": "a", "validate": "a", "debug": "a"}},
            "task kind",
        ),
        ({"escalation": {"a": "a"}}, "cycle"),
        ({"escalate_on": "rework_cycle"}, "escalate_on"),
    ],
)
def test_malformed_profiles_are_refused(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    _write(tmp_path, "bad.json", _document(**changes))
    with pytest.raises(RoutingProfileError, match=message):
        JsonRoutingProfiles(tmp_path)


def test_a_profile_defined_twice_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "one.json", _document())
    _write(tmp_path, "two.json", _document())
    with pytest.raises(RoutingProfileError, match="twice"):
        JsonRoutingProfiles(tmp_path)


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingProfileError, match="no routing profiles"):
        JsonRoutingProfiles(tmp_path)


def test_an_unreadable_directory_or_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingProfileError, match="cannot read"):
        JsonRoutingProfiles(tmp_path / "missing")
    (tmp_path / "latin1.json").write_bytes(b'{"name": "caf\xe9"}')
    with pytest.raises(RoutingProfileError, match="cannot read"):
        JsonRoutingProfiles(tmp_path)
