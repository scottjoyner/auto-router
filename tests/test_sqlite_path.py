"""Contract tests for the shared SQLite-URL to filesystem-path resolver.

``sqlite_path.sqlite_path`` replaced six byte-identical ``_path_from_url`` methods
(``ledger``, ``service_store``, ``memory_store``, ``signal_registry``,
``model_registry``, ``event_outbox``). These tests pin every accepted URL form,
including the two quirks the consolidation deliberately preserves, and assert each
persistence root resolves through the shared helper.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest

from auto_router import (
    event_outbox,
    ledger,
    memory_store,
    model_registry,
    service_store,
    signal_registry,
)
from auto_router.sqlite_path import sqlite_path

PERSISTENCE_MODULES = (
    ledger,
    service_store,
    memory_store,
    signal_registry,
    model_registry,
    event_outbox,
)


def _pre_consolidation_reference(database_url: str) -> Path:
    """The algorithm as it appeared in all six roots before consolidation.

    Kept here verbatim so the shared helper can be proven equivalent rather than
    merely similar.
    """

    if database_url.startswith("sqlite:///./"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite:////"):
        return Path(urlparse(database_url).path)
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    return Path(database_url)


# (url, expected string form)
CASES: tuple[tuple[str, str], ...] = (
    # Relative, explicitly marked with "./" -- cwd-relative.
    ("sqlite:///./data/router.sqlite3", "data/router.sqlite3"),
    ("sqlite:///./router.sqlite3", "router.sqlite3"),
    # Four slashes. PRESERVED QUIRK: urlparse leaves the path as "//data/...", and
    # POSIX Path keeps the leading double slash when stringified. It is the same
    # file, but the string form is not "/data/...".
    ("sqlite:////data/router.sqlite3", "//data/router.sqlite3"),
    ("sqlite:////tmp/opencode/ar-data/router.sqlite3", "//tmp/opencode/ar-data/router.sqlite3"),
    # Bare three-slash form.
    ("sqlite:///router.sqlite3", "router.sqlite3"),
    ("sqlite:///nested/dir/router.sqlite3", "nested/dir/router.sqlite3"),
    # Not a recognised URL -- passed to Path() verbatim, as before.
    ("/already/a/path.sqlite3", "/already/a/path.sqlite3"),
    ("relative.sqlite3", "relative.sqlite3"),
    ("", "."),
    ("postgresql://host/db", "postgresql:/host/db"),
    ("memory", "memory"),
)


@pytest.mark.parametrize(("url", "expected"), CASES)
def test_resolves_each_url_form(url: str, expected: str) -> None:
    assert str(sqlite_path(url)) == expected


@pytest.mark.parametrize(("url", "expected"), CASES)
def test_shared_helper_is_equivalent_to_the_pre_consolidation_algorithm(
    url: str,
    expected: str,
) -> None:
    """Equivalence against the verbatim original, for every form."""

    reference = _pre_consolidation_reference(url)
    resolved = sqlite_path(url)
    assert str(resolved) == expected
    assert str(reference) == expected
    assert resolved == reference


def test_dot_slash_prefix_is_stripped_only_when_present() -> None:
    # The "./" branch is checked first, so "./" is removed but a plain relative
    # path is returned unchanged.
    assert str(sqlite_path("sqlite:///./data/x.sqlite3")) == "data/x.sqlite3"
    assert str(sqlite_path("sqlite:///data/x.sqlite3")) == "data/x.sqlite3"


def test_four_slash_form_addresses_an_absolute_location() -> None:
    resolved = sqlite_path("sqlite:////data/router.sqlite3")
    assert resolved.is_absolute()
    # POSIX collapses "//" to "/" for filesystem purposes; the string keeps it.
    assert str(resolved).lstrip("/") == "data/router.sqlite3"


@pytest.mark.parametrize("module", PERSISTENCE_MODULES, ids=lambda m: m.__name__)
def test_each_root_delegates_to_the_shared_helper(module) -> None:
    assert not hasattr(module, "_path_from_url"), (
        f"{module.__name__} still defines a private _path_from_url"
    )
    assert module.sqlite_path is sqlite_path


STORE_FACTORIES = {
    "UsageLedger": ledger,
    "ServiceStatusStore": service_store,
    "MemoryStore": memory_store,
    "ContextSignalStore": signal_registry,
    "ModelRegistryStore": model_registry,
    "EventOutbox": event_outbox,
}


@pytest.mark.parametrize("factory_name", sorted(STORE_FACTORIES))
def test_store_factory_creates_the_file_at_the_resolved_relative_path(
    factory_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = STORE_FACTORIES[factory_name]
    factory = getattr(module, factory_name)
    name = f"{factory_name.lower()}.sqlite3"
    monkeypatch.chdir(tmp_path)

    store = factory(f"sqlite:///./{name}")

    assert Path(store.database_path) == Path(name)
    assert Path(name).exists(), "the database file must be created on the resolved path"
    connection = sqlite3.connect(str(store.database_path))
    assert connection is not None
    connection.close()


@pytest.mark.parametrize("factory_name", sorted(STORE_FACTORIES))
def test_store_factory_creates_the_file_at_the_resolved_absolute_path(
    factory_name: str,
    tmp_path: Path,
) -> None:
    module = STORE_FACTORIES[factory_name]
    factory = getattr(module, factory_name)
    name = f"{factory_name.lower()}.sqlite3"

    store = factory(f"sqlite:////{tmp_path}/{name}")

    assert Path(store.database_path).is_absolute()
    assert Path(store.database_path).exists(), "absolute URLs must not be created in cwd"
    assert not (Path.cwd() / name).exists()
