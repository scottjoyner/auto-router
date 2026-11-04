"""Synthetic isolated handoff tests: no fleet network, secrets or execution."""

from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "research/fleet_exec_durable_handoff.py"
SPEC = importlib.util.spec_from_file_location("fleet_exec_handoff", SOURCE)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

DIGEST = "a" * 64
RESULT = "b" * 64


class ResearchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.db"
        self.ledger = mod.HandoffJournal(self.path)
        self.addCleanup(self.ledger.close)
        self.assertEqual(self.ledger.synthetic_epoch("controller_a", 1, mod.SYNTHETIC_ACK), 0)

    def queue(self, call="call-1", digest=DIGEST):
        return self.ledger.enqueue("controller_a", 1, call, "destroyer", digest)

    def delivered(self):
        self.queue()
        return self.ledger.advance("controller_a", 1, "call-1", "destroyer", "DELIVERED")

    def acked(self):
        self.delivered()
        return self.ledger.advance("controller_a", 1, "call-1", "destroyer", "ACKED")

    def result(self):
        self.acked()
        return self.ledger.advance(
            "controller_a", 1, "call-1", "destroyer", "RESULT_RECORDED", RESULT
        )

    def test_queue_is_durable_across_connection_restart(self):
        self.queue()
        second = mod.HandoffJournal(self.path)
        self.addCleanup(second.close)
        self.assertEqual(second.snapshot()["counts"], {"QUEUED": 1})

    def test_ack_is_durable_across_connection_restart(self):
        self.acked()
        second = mod.HandoffJournal(self.path)
        self.addCleanup(second.close)
        self.assertEqual(second.snapshot()["counts"], {"ACKED": 1})

    def test_result_durable_across_connection_restart(self):
        self.result()
        second = mod.HandoffJournal(self.path)
        self.addCleanup(second.close)
        self.assertEqual(second.snapshot()["counts"], {"RESULT_RECORDED": 1})

    def test_idempotent_enqueue_does_not_redispatch_or_duplicate_event(self):
        self.queue()
        events = self.ledger.snapshot()["event_count"]
        self.assertEqual(self.queue(), "QUEUED")
        self.assertEqual(events, self.ledger.snapshot()["event_count"])

    def test_same_call_id_different_payload_is_rejected(self):
        self.queue()
        with self.assertRaises(mod.Denied):
            self.queue(digest=RESULT)

    def test_same_call_id_different_device_is_rejected(self):
        self.queue()
        with self.assertRaises(mod.Denied):
            self.ledger.enqueue("controller_a", 1, "call-1", "other", DIGEST)

    def test_missing_controller_ack_is_rejected(self):
        with self.assertRaises(mod.Denied):
            self.ledger.synthetic_epoch("controller_b", 2, "")

    def test_equal_epoch_takeover_is_denied(self):
        with self.assertRaises(mod.Denied):
            self.ledger.synthetic_epoch("controller_b", 1, mod.SYNTHETIC_ACK)

    def test_old_controller_cannot_queue_after_takeover(self):
        self.ledger.synthetic_epoch("controller_b", 2, mod.SYNTHETIC_ACK)
        with self.assertRaises(mod.Denied):
            self.queue()

    def test_old_controller_cannot_record_result_after_takeover(self):
        self.acked()
        self.ledger.synthetic_epoch("controller_b", 2, mod.SYNTHETIC_ACK)
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "destroyer", "RESULT_RECORDED", RESULT)

    def test_takeover_quarantines_queued_and_delivered_and_acked(self):
        self.queue("call-queued")
        self.queue("call-delivered")
        self.ledger.advance("controller_a", 1, "call-delivered", "destroyer", "DELIVERED")
        self.queue("call-acked")
        self.ledger.advance("controller_a", 1, "call-acked", "destroyer", "DELIVERED")
        self.ledger.advance("controller_a", 1, "call-acked", "destroyer", "ACKED")
        count = self.ledger.synthetic_epoch("controller_b", 2, mod.SYNTHETIC_ACK)
        self.assertEqual(count, 3)
        self.assertEqual(self.ledger.snapshot()["counts"], {"QUARANTINED": 3})

    def test_quarantined_call_cannot_replay_under_new_epoch(self):
        self.delivered()
        self.ledger.synthetic_epoch("controller_b", 2, mod.SYNTHETIC_ACK)
        with self.assertRaises(mod.Denied):
            self.ledger.enqueue("controller_b", 2, "call-1", "destroyer", DIGEST)
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_b", 2, "call-1", "destroyer", "ACKED")

    def test_completed_result_is_not_quarantined(self):
        self.result()
        count = self.ledger.synthetic_epoch("controller_b", 2, mod.SYNTHETIC_ACK)
        self.assertEqual(count, 0)
        self.assertEqual(self.ledger.snapshot()["counts"], {"RESULT_RECORDED": 1})

    def test_cannot_ack_before_delivery(self):
        self.queue()
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "destroyer", "ACKED")

    def test_cannot_record_result_before_ack(self):
        self.delivered()
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "destroyer", "RESULT_RECORDED", RESULT)

    def test_same_result_is_idempotent(self):
        self.result()
        count = self.ledger.snapshot()["event_count"]
        self.assertEqual(
            self.ledger.advance(
                "controller_a", 1, "call-1", "destroyer", "RESULT_RECORDED", RESULT
            ),
            "RESULT_RECORDED",
        )
        self.assertEqual(count, self.ledger.snapshot()["event_count"])

    def test_different_result_for_same_call_is_refused(self):
        self.result()
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "destroyer", "RESULT_RECORDED", DIGEST)

    def test_cross_device_ack_is_denied(self):
        self.delivered()
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "xwing", "ACKED")

    def test_unrecognized_transition_is_denied(self):
        self.queue()
        with self.assertRaises(mod.Denied):
            self.ledger.advance("controller_a", 1, "call-1", "destroyer", "EXECUTE")

    def test_bad_digest_is_denied(self):
        with self.assertRaises(mod.Denied):
            self.ledger.enqueue("controller_a", 1, "call-1", "destroyer", "not-hash")

    def test_bad_identity_is_denied(self):
        with self.assertRaises(mod.Denied):
            self.ledger.enqueue("controller_a", 1, "../unsafe", "destroyer", DIGEST)

    def test_local_sqlite_atomic_same_epoch_competing_promotions(self):
        outcomes = []
        barrier = threading.Barrier(2)

        def attempt(owner):
            # A SQLite handle is thread-affine; each contender independently
            # opens the SAME database file, like two local controller processes.
            local = mod.HandoffJournal(self.path)
            try:
                barrier.wait()
                try:
                    local.synthetic_epoch(owner, 2, mod.SYNTHETIC_ACK)
                    outcomes.append("accepted")
                except mod.Denied:
                    outcomes.append("denied")
            finally:
                local.close()

        a = threading.Thread(target=attempt, args=("controller_b",))
        b = threading.Thread(target=attempt, args=("controller_c",))
        a.start()
        b.start()
        a.join(3)
        b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertCountEqual(outcomes, ["accepted", "denied"])

    def test_separate_uncoordinated_databases_cannot_prove_cross_host_fencing(self):
        path2 = Path(self.temp.name) / "otherhost.db"
        independent = mod.HandoffJournal(path2)
        self.addCleanup(independent.close)
        self.assertEqual(independent.synthetic_epoch("controller_other", 1, mod.SYNTHETIC_ACK), 0)
        # Both independent stores can claim epoch 1; this is *not*
        # a production split-brain solution, even though each DB is durable.
        self.assertEqual(self.ledger.snapshot()["controller"]["epoch"], 1)
        self.assertEqual(independent.snapshot()["controller"]["epoch"], 1)
        self.assertFalse(independent.snapshot()["production_fencing_proven"])

    def test_research_only_flags_never_claim_production_safety(self):
        flags = self.ledger.snapshot()
        self.assertTrue(flags["research_only"])
        self.assertFalse(flags["external_custody_proven"])
        self.assertFalse(flags["production_fencing_proven"])
        self.assertFalse(flags["at_most_once_external_effects_proven"])

    def test_invalid_relative_path_denied(self):
        with self.assertRaises(mod.Denied):
            mod.HandoffJournal(Path("relative.db"))

    def test_database_rejects_symlink_entry(self):
        p = Path(self.temp.name) / "link.db"
        p.symlink_to(self.path)
        with self.assertRaises(mod.Denied):
            mod.HandoffJournal(p)

    def test_database_is_full_sync_wal(self):
        self.assertEqual(self.ledger.db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.ledger.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")


if __name__ == "__main__":
    unittest.main()
