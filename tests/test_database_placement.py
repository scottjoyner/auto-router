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
        "sqlite:///data/router.sqlite3",
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
