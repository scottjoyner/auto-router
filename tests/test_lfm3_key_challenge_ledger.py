"""Synthetic tests for verifier-only local nonce issue-and-spend ledger."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import fcntl
import os
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research.lfm3_top5.key_challenge_ledger import (
    issue_to_private_ledger, consume_once,
)
from research.lfm3_top5.key_possession_contract import (
    issue_local_challenge, challenge_signing_bytes,
)
from research.lfm3_top5.prospective_holdout_gate import sha
from tests.test_lfm3_signer_payload_handoff import fixtures, private_file


def setup_case(root: Path, role: str = "author"):
    _, roster, keys = fixtures()
    ledger = root / "verifier-only-ledger"
    ledger.mkdir(mode=0o700)
    challenge = root / "challenge.json"
    payload = root / "signing.bin"
    status = issue_to_private_ledger(roster, role, str(ledger),
                                     str(challenge), str(payload))
    assert status["decision"] == "PASS_OFFLINE_LOCAL_CHALLENGE_ISSUED_ONLY", status
    source = json.loads(challenge.read_text())
    signature = keys[role].sign(payload.read_bytes())
    return roster, ledger, challenge, payload, source, signature


class LocalNonceLedger(unittest.TestCase):
    def test_issued_then_consumed_exactly_once_for_each_role(self):
        for role in ("author", "reviewer", "witness"):
            with self.subTest(role=role), tempfile.TemporaryDirectory(prefix="lfm3-ledger-") as path:
                roster, ledger, chal, payload, source, sig = setup_case(Path(path), role)
                self.assertEqual(stat.S_IMODE(chal.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o600)
                first = consume_once(roster, role, source, sig, str(ledger))
                self.assertEqual(first["decision"],
                                 "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")
                self.assertTrue(first["local_one_time_claim_recorded"])
                self.assertTrue(first["local_issuance_record_matched"])
                for field in ("independent_challenge_issuance_verified",
                              "challenge_freshness_and_single_use_verified",
                              "independent_human_identity_verified",
                              "signer_organizational_independence_verified",
                              "permitted_to_infer", "production_authorized"):
                    self.assertFalse(first[field], field)
                self.assertEqual(consume_once(roster, role, source, sig,
                                              str(ledger))["decision"], "HOLD")
                digest = sha(source)
                self.assertEqual(stat.S_IMODE(
                    (ledger / ("spent-" + digest + ".json")).stat().st_mode), 0o600)

    def test_no_unissued_challenge_can_be_consumed(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-unissued-") as path:
            root = Path(path)
            _, roster, keys = fixtures()
            ledger = root / "ledger"
            ledger.mkdir(mode=0o700)
            _, challenge = issue_local_challenge(roster, "author")
            _, payload = challenge_signing_bytes(roster, "author", challenge)
            sig = keys["author"].sign(payload)
            out = consume_once(roster, "author", challenge, sig, str(ledger))
            self.assertEqual(out["decision"], "HOLD")
            self.assertFalse(out["local_issuance_record_matched"])

    def test_invalid_signature_does_not_spend(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-invalid-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            bad = consume_once(roster, "author", c, bytes(64), str(ledger))
            self.assertEqual(bad["decision"], "HOLD")
            self.assertFalse((ledger / ("spent-" + sha(c) + ".json")).exists())
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")

    def test_cross_role_and_wrong_nonce_do_not_spend(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-cross-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            self.assertEqual(consume_once(roster, "reviewer", c, sig,
                                          str(ledger))["decision"], "HOLD")
            other = dict(c)
            other["nonce_hex"] = "f" * 64
            self.assertEqual(consume_once(roster, "author", other, sig,
                                          str(ledger))["decision"], "HOLD")
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")

    def test_concurrent_redeemers_cannot_both_pass(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-concurrent-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            def claim(_):
                return consume_once(roster, "author", c, sig, str(ledger))["decision"]
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(claim, range(8)))
            self.assertEqual(results.count("PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY"), 1)
            self.assertEqual(results.count("HOLD"), 7)

    def test_corrupt_record_or_world_readable_record_holds(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-corrupt-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            record = ledger / ("issued-" + sha(c) + ".json")
            original = record.read_bytes()
            body = json.loads(original)
            body["role"] = "witness"
            record.write_text(json.dumps(body))
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"], "HOLD")
            record.write_bytes(original)
            record.chmod(0o644)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"], "HOLD")
            record.chmod(0o600)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")

    def test_unsafe_directory_and_symlinked_ledger_holds(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-unsafe-") as path:
            root = Path(path)
            roster, ledger, _, _, c, sig = setup_case(root)
            ledger.chmod(0o755)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"], "HOLD")
            ledger.chmod(0o700)
            alias = root / "alias"
            alias.symlink_to(ledger, target_is_directory=True)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(alias))["decision"], "HOLD")
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")

    def test_uncertain_spent_fsync_does_not_reenable_replay(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-uncertain-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            with patch("research.lfm3_top5.key_challenge_ledger.os.fsync",
                       side_effect=OSError("synthetic durability failure")):
                result = consume_once(roster, "author", c, sig, str(ledger))
            self.assertEqual(result["decision"], "HOLD")
            self.assertTrue((ledger / ("spent-" + sha(c) + ".json")).exists())
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"], "HOLD")

    def test_unrecorded_partial_issue_not_redeemable(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-incomplete-") as path:
            root = Path(path)
            _, roster, keys = fixtures()
            ledger = root / "ledger"
            ledger.mkdir(mode=0o700)
            challenge = root / "challenge.json"
            payload = root / "payload.bin"
            with patch("research.lfm3_top5.key_challenge_ledger._owned_create",
                       side_effect=OSError("synthetic ledger full")):
                out = issue_to_private_ledger(roster, "author", str(ledger),
                                              str(challenge), str(payload))
            self.assertEqual(out["decision"], "HOLD")
            if challenge.exists() and payload.exists():
                c = json.loads(challenge.read_text())
                sig = keys["author"].sign(payload.read_bytes())
                self.assertEqual(consume_once(roster, "author", c, sig,
                                              str(ledger))["decision"], "HOLD")

    def test_consumer_cannot_see_issued_record_before_issuer_fsync(self):
        # Reproduce the publication window deterministically: the issuer has
        # written the complete issuance marker but has not finished its
        # directory fsync yet. Consume must HOLD until the issuer releases
        # the pinned directory lock, then a normal first consume may PASS.
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-publish-race-") as path:
            root = Path(path)
            _, roster, keys = fixtures()
            ledger = root / "ledger"
            ledger.mkdir(mode=0o700)
            challenge = root / "challenge.json"
            payload = root / "payload.bin"
            marker_created = Event()
            release_issuer = Event()
            from research.lfm3_top5 import key_challenge_ledger as module
            original_create = module._owned_create

            def pause_after_issue_write(fd, name, body, created):
                original_create(fd, name, body, created)
                if name.startswith("issued-"):
                    marker_created.set()
                    if not release_issuer.wait(timeout=8):
                        raise TimeoutError("synthetic issuer fsync delay")

            with patch.object(module, "_owned_create",
                              side_effect=pause_after_issue_write):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(issue_to_private_ledger, roster,
                        "author", str(ledger), str(challenge), str(payload))
                    try:
                        self.assertTrue(marker_created.wait(timeout=5))
                        self.assertTrue(challenge.is_file())
                        self.assertTrue(payload.is_file())
                        issued = json.loads(challenge.read_text())
                        signature = keys["author"].sign(payload.read_bytes())
                        denied = consume_once(roster, "author", issued,
                                              signature, str(ledger))
                        self.assertEqual(denied["decision"], "HOLD")
                        self.assertFalse(denied["local_one_time_claim_recorded"])
                        self.assertFalse((ledger / ("spent-" + sha(issued) + ".json")).exists())
                    finally:
                        release_issuer.set()
                    self.assertEqual(future.result(timeout=5)["decision"],
                                     "PASS_OFFLINE_LOCAL_CHALLENGE_ISSUED_ONLY")
            first = consume_once(roster, "author", issued, signature, str(ledger))
            self.assertEqual(first["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")
            self.assertEqual(consume_once(roster, "author", issued,
                                          signature, str(ledger))["decision"], "HOLD")

    def test_external_ledger_lock_contention_blocks_issue_and_consume(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-contention-") as path:
            root = Path(path)
            roster, ledger, _, _, c, sig = setup_case(root)
            from research.lfm3_top5.key_challenge_ledger import _ledger_fd
            fd = _ledger_fd(str(ledger))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(consume_once(roster, "author", c, sig,
                                              str(ledger))["decision"], "HOLD")
                challenge = root / "other-challenge.json"
                payload = root / "other-payload.bin"
                out = issue_to_private_ledger(roster, "reviewer", str(ledger),
                                              str(challenge), str(payload))
                self.assertEqual(out["decision"], "HOLD")
                self.assertFalse(challenge.exists())
                self.assertFalse(payload.exists())
            finally:
                os.close(fd)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"],
                             "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY")

    def test_cli_issue_consume_then_replay_holds(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-cli-") as path:
            root = Path(path)
            _, roster, keys = fixtures()
            private_file(root / "roster.json", roster)
            ledger = root / "ledger"
            ledger.mkdir(mode=0o700)
            ch = root / "chall.json"
            payload = root / "payload.bin"
            sig = root / "sig.bin"
            issue = [sys.executable, "-m", "scripts.lfm3_key_challenge_ledger",
                     "issue", "--key-roster", str(root / "roster.json"),
                     "--role", "author", "--ledger-dir", str(ledger),
                     "--challenge-output", str(ch), "--payload-output", str(payload)]
            prepared = subprocess.run(issue, capture_output=True, text=True, timeout=15)
            self.assertEqual(prepared.returncode, 0, (prepared.stdout, prepared.stderr))
            private_file(sig, keys["author"].sign(payload.read_bytes()))
            consume = [sys.executable, "-m", "scripts.lfm3_key_challenge_ledger",
                       "consume", "--key-roster", str(root / "roster.json"),
                       "--role", "author", "--ledger-dir", str(ledger),
                       "--challenge", str(ch), "--signature", str(sig)]
            first = subprocess.run(consume, capture_output=True, text=True, timeout=15)
            self.assertEqual(first.returncode, 0, (first.stdout, first.stderr))
            self.assertFalse(json.loads(first.stdout)["independent_human_identity_verified"])
            self.assertFalse(json.loads(first.stdout)["production_authorized"])
            second = subprocess.run(consume, capture_output=True, text=True, timeout=15)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(json.loads(second.stdout)["decision"], "HOLD")
            self.assertEqual(subprocess.run(issue, capture_output=True, timeout=15).returncode, 2)

    def test_preexisting_spent_marker_blocks_even_if_malformed(self):
        with tempfile.TemporaryDirectory(prefix="lfm3-ledger-preblock-") as path:
            roster, ledger, _, _, c, sig = setup_case(Path(path))
            marker = ledger / ("spent-" + sha(c) + ".json")
            marker.write_text("not valid json")
            marker.chmod(0o600)
            self.assertEqual(consume_once(roster, "author", c, sig,
                                          str(ledger))["decision"], "HOLD")
            self.assertEqual(marker.read_text(), "not valid json")


if __name__ == "__main__":
    unittest.main()
