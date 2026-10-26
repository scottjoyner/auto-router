"""Offline SQLite fixtures for the non-promoting event freshness witness."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "router_event_outbox_freshness.py"
)
spec = importlib.util.spec_from_file_location("router_event_outbox_freshness", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

NOW = 1_800_000_000
CUTOFF = NOW - 3600
FAKE = "FAKE_SECRET_SHOULD_NEVER_REACH_WITNESS_OUTPUT"


class TestReadOnlyOutboxFreshness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="outbox-witness-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "outbox.sqlite3"
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE event_outbox("
            "status TEXT NOT NULL,updated_at INTEGER NOT NULL,"
            "last_error TEXT,payload_json TEXT,idempotency_key TEXT)"
        )
        db.commit()
        db.close()

    def insert(self, status, when, error=None):
        db = sqlite3.connect(self.path)
        db.execute(
            "INSERT INTO event_outbox(status,updated_at,last_error,payload_json,idempotency_key)"
            " VALUES(?,?,?,?,?)",
            (status, when, error, FAKE, FAKE),
        )
        db.commit()
        db.close()

    def run_witness(self):
        return module.inspect(self.path, cutoff=CUTOFF, now=NOW)

    def assert_hold(self, reason):
        r = self.run_witness()
        self.assertEqual(r["verdict"], "HOLD")
        self.assertEqual(r["reason"], reason)
        self.assertFalse(r["deployment_authorized"])
        self.assertFalse(r["authenticated_delivery_proven"])
        self.assertFalse(r["outbox_replayed"])
        self.assertEqual(r["events_mutated"], 0)
        self.assertEqual(r["event_payloads_read"], 0)
        self.assertEqual(r["raw_errors_read"], 0)
        self.assertNotIn(FAKE, json.dumps(r))

    def test_empty_database_has_no_delivery_proof(self):
        self.assert_hold("no_recent_delivery_proof")

    def test_historical_401_does_not_imply_current_outage(self):
        self.insert("dead_letter", NOW - 100000, "AssistX returned HTTP 401 " + FAKE)
        self.insert("delivered", NOW - 30)
        r = self.run_witness()
        self.assertEqual(r["verdict"], "FRESHNESS_OBSERVED_ONLY")
        self.assertEqual(r["new_http_401_dead_letters_since_cutoff"], 0)
        self.assertEqual(r["counts"]["dead_letter"], 1)
        self.assertEqual(r["counts"]["delivered"], 1)
        self.assertFalse(r["authenticated_delivery_proven"])
        self.assertNotIn(FAKE, json.dumps(r))

    def test_current_401_is_hold_even_with_recent_delivery(self):
        self.insert("delivered", NOW - 4)
        self.insert("dead_letter", NOW - 2, "AssistX returned HTTP 401 "+FAKE)
        self.assert_hold("new_auth_denied_dead_letters")

    def test_other_new_dead_letter_is_hold(self):
        self.insert("delivered", NOW - 4)
        self.insert("dead_letter", NOW - 2, "AssistX returned HTTP 503 " + FAKE)
        self.assert_hold("new_dead_letters")

    def test_pending_is_not_a_drained_queue(self):
        self.insert("delivered", NOW - 4)
        self.insert("pending", NOW - 1)
        self.assert_hold("pending_or_retry_not_drained")

    def test_retry_is_not_a_drained_queue(self):
        self.insert("delivered", NOW - 4)
        self.insert("retry", NOW - 1)
        self.assert_hold("pending_or_retry_not_drained")

    def test_stale_delivery_is_hold_even_when_zero_new_errors(self):
        self.insert("delivered", NOW - 100000)
        self.assert_hold("no_recent_delivery_proof")

    def test_future_timestamp_is_hold(self):
        self.insert("delivered", NOW + 900)
        self.assert_hold("invalid_or_future_record_timestamp")

    def test_unknown_status_is_hold(self):
        self.insert("not_a_real_status", NOW - 2)
        self.assert_hold("unexpected_outbox_status")

    def test_missing_file_is_hold(self):
        self.path.unlink()
        self.assert_hold("database_not_accessible")

    def test_symlink_database_is_hold(self):
        new = self.path.with_suffix(".safe")
        self.path.rename(new)
        self.path.symlink_to(new)
        self.assert_hold("database_symlink_component")

    def test_symlink_parent_is_hold(self):
        old = self.path.parent
        new = old.with_name(old.name+"-other")
        old.rename(new)
        old.symlink_to(new, target_is_directory=True)
        try:
            self.assert_hold("database_symlink_component")
        finally:
            # Restore the original real directory before TemporaryDirectory
            # tries to recursively remove it. Never leave a scratch symlink.
            old.unlink()
            new.rename(old)

    def test_directory_not_a_database_is_hold(self):
        self.path.unlink()
        self.path.mkdir()
        self.assert_hold("database_not_regular")

    def test_invalid_schema_is_hold(self):
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE unrelated(a TEXT)")
        db.close()
        self.assert_hold("outbox_table_missing")

    def test_missing_columns_is_hold(self):
        self.path.unlink()
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE event_outbox(status TEXT)")
        db.close()
        self.assert_hold("outbox_columns_missing")

    def test_cutoff_after_now_is_hold(self):
        r = module.inspect(self.path, cutoff=NOW+1, now=NOW)
        self.assertEqual(r["reason"], "invalid_time_window")

    def test_never_changes_database_bytes_in_empty_fixture(self):
        baseline = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.run_witness()
        after = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(baseline, after)

    def test_cli_success_means_observation_only(self):
        self.insert("delivered", NOW - 4)
        with patch.object(sys, "argv", [
            "outbox", "--database-path", str(self.path),
            "--since-unix", str(CUTOFF),
            "--now-unix", str(NOW),
        ]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = module.main()
        self.assertEqual(rc, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["verdict"], "FRESHNESS_OBSERVED_ONLY")
        self.assertFalse(report["deployment_authorized"])

    def test_cli_fails_on_stale_database(self):
        with patch.object(sys, "argv", [
            "outbox", "--database-path", str(self.path),
            "--since-unix", str(CUTOFF),
            "--now-unix", str(NOW),
        ]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = module.main()
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(stdout.getvalue())["reason"],
                         "no_recent_delivery_proof")


if __name__ == "__main__":
    unittest.main()
