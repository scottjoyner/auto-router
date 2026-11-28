import os

import pytest

from auto_router.settings import validate_database_placement


def test_relative_sqlite_path_is_rejected_when_production_persistent_root_is_required(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="container writable layer"):
        validate_database_placement(
            "sqlite:///./data/router.sqlite3",
            expected_root="/data",
            required=True,
        )


def test_absolute_sqlite_path_under_persistent_root_is_accepted(tmp_path):
    assert validate_database_placement(
        "sqlite:////data/router.sqlite3",
        expected_root="/data",
        required=True,
    )["persistent"] is True


def test_in_memory_sqlite_requires_explicit_test_context():
    assert validate_database_placement(
        "sqlite:///:memory:",
        expected_root="/data",
        required=False,
        test_context=True,
    )["persistent"] is False


def test_guard_reports_resolution_and_persistence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = validate_database_placement(
        "sqlite:///./data/router.sqlite3",
        expected_root="/data",
        required=False,
    )

    assert result["configured_url"] == "sqlite:///./data/router.sqlite3"
    assert result["resolved_path"] == os.path.abspath("data/router.sqlite3")
    assert result["expected_persistent_root"] == "/data"
    assert result["persistent"] is False


def test_three_slash_relative_data_path_reproduces_and_rejects_container_bug(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="container writable layer"):
        validate_database_placement("sqlite:///data/router.sqlite3", expected_root="/data", required=True)


def test_guard_and_real_ledger_resolve_same_path(tmp_path, monkeypatch):
    from auto_router.ledger import UsageLedger
    from auto_router.event_outbox import EventOutbox
    monkeypatch.chdir(tmp_path)
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    for url in ("sqlite:///data/router.sqlite3", "sqlite:///./data/router.sqlite3", "sqlite:////data/router.sqlite3", "sqlite:///" + str(persistent / "router.sqlite3")):
        report = validate_database_placement(url, expected_root=str(persistent), required=False)
        assert report["resolved_path"] == str(UsageLedger._path_from_url(None, url).resolve())
        assert report["resolved_path"] == str(EventOutbox._path_from_url(None, url).resolve())


def test_guard_rejects_symlink_escape_and_root_itself(tmp_path):
    root = tmp_path / "persistent"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    for path in (root / "escape" / "router.sqlite3", root):
        with pytest.raises(ValueError, match="container writable layer"):
            validate_database_placement("sqlite:///" + str(path), expected_root=str(root), required=True)


def test_production_rejects_unsupported_database_url(tmp_path):
    with pytest.raises(ValueError, match="canonical absolute SQLite"):
        validate_database_placement("postgresql://example.invalid/db", expected_root=str(tmp_path), required=True)


def test_production_rejects_relative_even_if_under_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="canonical absolute SQLite"):
        validate_database_placement("sqlite:///router.sqlite3", expected_root=str(tmp_path), required=True)


@pytest.mark.parametrize("suffix", ["?mode=ro", "#fragment", ";params"])
def test_production_rejects_ambiguous_url_components(tmp_path, suffix):
    with pytest.raises(ValueError, match="canonical absolute SQLite"):
        validate_database_placement("sqlite:///" + str(tmp_path / "router.sqlite3") + suffix, expected_root=str(tmp_path), required=True)


def test_canonical_absolute_path_agrees_across_all_stores(tmp_path):
    from auto_router.ledger import UsageLedger
    from auto_router.event_outbox import EventOutbox
    from auto_router.memory_store import MemoryStore
    from auto_router.model_registry import ModelRegistryStore
    from auto_router.request_idempotency import _database_path
    from auto_router.service_store import ServiceStatusStore
    from auto_router.signal_registry import ContextSignalStore
    target=tmp_path / "router.sqlite3"
    url="sqlite:///"+str(target)
    expected=validate_database_placement(url,expected_root=str(tmp_path),required=True)["resolved_path"]
    for path in (UsageLedger._path_from_url(None,url), EventOutbox._path_from_url(None,url), MemoryStore._path_from_url(url), ModelRegistryStore._path_from_url(None,url), _database_path(url), ServiceStatusStore._path_from_url(None,url), ContextSignalStore._path_from_url(None,url)):
        assert str(path.resolve())==expected
