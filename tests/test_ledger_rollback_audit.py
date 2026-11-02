"""Synthetic read-only rollback rehearsal tests; never access production."""
import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/audit-ledger-provenance-rollback.py"
SPEC = importlib.util.spec_from_file_location("rollback_audit", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture(autouse=True)
def _synthetic_memory_floor():
    # Unit tests are tiny and must not require 8 GiB on hosted CI.
    # The live audit itself still enforces the physical memory gate.
    with patch.object(module, "memory_available", return_value=64 * 1024**3):
        yield


def test_wal_snapshot_sees_committed_rows_without_writing_source(tmp_path):
    path=tmp_path/"wal.sqlite3"
    _make_db(path)
    writer=sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]=="wal"
        for table in module.TABLES:
            writer.execute(
                f"INSERT INTO {table}(id,input_tokens,output_tokens,total_tokens,request_id) "
                "VALUES (2,9,3,12,'committed-in-WAL')"
            )
        writer.commit()
        before=module.signature(writer)
        result=module.audit(path)
        assert result["status"]=="PASS"
        assert all(result["checks"].values())
        assert all(x["rows"]==2 for x in result["source_tables"].values())
        assert module.signature(writer)==before
        assert "committed-in-WAL" not in repr(result)
    finally:
        writer.close()


def _make_db(path, *, with_provenance=False):
    with sqlite3.connect(path) as conn:
        for table in module.TABLES:
            conn.execute(f"""CREATE TABLE {table} (
                id INTEGER PRIMARY KEY,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                request_id TEXT NOT NULL
                {", token_usage_source TEXT NOT NULL DEFAULT 'unknown'"
                  if with_provenance else ""}
            )""")
            conn.execute(f"CREATE INDEX idx_{table}_request ON {table}(request_id)")
            conn.execute(
                f"INSERT INTO {table}(id,input_tokens,output_tokens,total_tokens,request_id) "
                "VALUES (1,101000,2000,103000,'old-quota')"
            )
        conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY, private_data BLOB)")
        conn.execute("INSERT INTO unrelated(id,private_data) VALUES(1,?)",
                     (b"private-must-not-leak",))


def test_real_synthetic_database_roundtrip_and_no_source_mutation(tmp_path):
    path=tmp_path/"previous.sqlite3"
    _make_db(path)
    with sqlite3.connect(path) as conn:
        before=module.signature(conn)
    result=module.audit(path)
    assert result["status"]=="PASS"
    assert all(result["checks"].values())
    assert result["source_writes"]==0
    assert result["source_rows_stored_to_disk"] is False
    assert result["source_tables"]["usage_events"]["rows"]==1
    assert result["source_tables"]["runtime_samples"]["indexes"]==1
    assert "old-quota" not in repr(result)
    assert "private-must-not-leak" not in repr(result)
    assert result["can_dispatch"] is False
    with sqlite3.connect(path) as conn:
        assert module.signature(conn)==before
        assert all("token_usage_source" not in module.columns(conn,t) for t in module.TABLES)


def test_provenance_schema_already_present_is_hold(tmp_path):
    path=tmp_path/"already.sqlite3"
    _make_db(path,with_provenance=True)
    with pytest.raises(ValueError,match="source_schema_already_has_provenance"):
        module.audit(path)


def test_source_symlink_cannot_be_opened(tmp_path):
    path=tmp_path/"db.sqlite3"
    _make_db(path)
    link=tmp_path/"link.sqlite3"
    link.symlink_to(path)
    with pytest.raises(ValueError,match="no_symlink"):
        module.audit(link)


def test_source_size_and_memory_floor_fail_before_connect(tmp_path):
    path=tmp_path/"previous.sqlite3"
    _make_db(path)
    with pytest.raises(ValueError,match="source_exceeds_bound"):
        module.audit(path,max_db_bytes=1)
    with patch.object(module,"memory_available",return_value=0):
        with pytest.raises(ValueError,match="insufficient_memory"):
            module.audit(path)


def test_no_accidental_table_arbitrary_sql():
    conn=sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError,match="invalid_table_name"):
            module.columns(conn,"usage_events; DROP TABLE unrelated")
        with pytest.raises(ValueError,match="invalid_table_name"):
            module.preserved_rows(conn,"other")
    finally:
        conn.close()


def test_copy_rollback_recovers_unrelated_schema_and_records(tmp_path):
    path=tmp_path/"sample.sqlite3"
    _make_db(path)
    with sqlite3.connect(path) as conn:
        initial=module.schema_digest(conn)
        data=conn.execute("SELECT private_data FROM unrelated").fetchone()[0]
    result=module.audit(path)
    assert result["checks"]["restore_recovered_original_schema_and_counters"]
    with sqlite3.connect(path) as conn:
        assert module.schema_digest(conn)==initial
        assert conn.execute("SELECT private_data FROM unrelated").fetchone()[0]==data
