"""Fail-closed checks for unsigned read-only production-blocker evidence."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fleet_authority_recovery_gate", ROOT / "experiments" / "fleet_authority_recovery_gate.py"
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
SAMPLE = ROOT / "experiments" / "fixtures" / "fleet_authority_observed_20261011.json"


def sample() -> dict:
    return json.loads(SAMPLE.read_text())


class TestRecoveryGate(unittest.TestCase):
    def test_observed_live_blockers_fail_closed(self):
        out = M.decide(sample())
        self.assertEqual(out["status"], "BLOCKED")
        self.assertFalse(out["can_dispatch"])
        self.assertFalse(out["can_promote"])
        self.assertFalse(out["execution_attested"])
        self.assertGreaterEqual(len(out["blockers"]), 10)

    def test_pin_expired_canonical_approval_root_cause(self):
        self.assertIn("CANONICAL_APPROVAL_EXPIRED", M.decide(sample())["blockers"])

    def test_all_four_fresh_evidence_families_absent(self):
        reasons = M.decide(sample())["blockers"]
        for name in (
            "NO_FRESH_ADMITTED_RUNTIME", "NO_FRESH_LOADED_MODEL",
            "NO_FRESH_ACCESS_PATH", "NO_FRESH_CAPACITY_OBSERVATION",
        ):
            self.assertIn(name, reasons)

    def test_legacy_admin_anonymous_exposure_not_hidden(self):
        reasons = M.decide(sample())["blockers"]
        self.assertIn("LEGACY_PROVIDER_ADMIN_ANONYMOUS_200", reasons)

    def test_fresh_catalog_route_not_yet_live(self):
        reasons = M.decide(sample())["blockers"]
        self.assertIn("EFFECTIVE_PROVIDER_CATALOG_NOT_DEPLOYED_OR_INACCESSIBLE",
                      reasons)

    def test_general_operational_readiness_does_not_override_stale_projection(self):
        out = M.decide(sample())
        self.assertIn("GENERAL_READINESS_NOT_EQUIVALENT_TO_FRESH_RUNTIME_AUTHORITY",
                      out["blockers"])
        self.assertFalse(out["can_dispatch"])

    def test_no_503_cannot_imply_fresh(self):
        d = sample()
        d["assistx"]["runtime_projection_http"] = 200
        out = M.decide(d)
        self.assertIn("CANONICAL_APPROVAL_EXPIRED", out["blockers"])
        self.assertIn("ROUTER_PROJECTION_STALE_OR_ERROR", out["blockers"])

    def test_unexpected_live_identifiers_refused(self):
        d = sample()
        d["owner_token"] = "not admissible"
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            M.decide(d)

    def test_inband_signed_claim_is_not_a_substitute(self):
        d = sample()
        d["kind"] = "signed_live_authority"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            M.decide(d)

    def test_incorrect_negative_counts_rejected(self):
        d = sample()
        d["neo4j"]["current_approved_paths"] = -1
        with self.assertRaisesRegex(ValueError, "invalid"):
            M.decide(d)

    def test_current_cannot_exceed_approved_count(self):
        d = sample()
        d["neo4j"]["current_approved_paths"] = 3
        with self.assertRaisesRegex(ValueError, "more current"):
            M.decide(d)

    def test_fake_ready_flags_not_integer(self):
        d = sample()
        d["assistx"]["operations_ready"] = 1
        with self.assertRaisesRegex(ValueError, "boolean"):
            M.decide(d)

    def test_nan_expiry_rejected(self):
        d = sample()
        d["neo4j"]["approval_expires_in_minutes"] = float("nan")
        with self.assertRaisesRegex(ValueError, "lifetime"):
            M.decide(d)

    def test_malformed_utc_rejected(self):
        d = sample()
        d["observed_at_utc"] = "2026-10-11 16:40"
        with self.assertRaisesRegex(ValueError, "UTC"):
            M.decide(d)

    def test_even_fully_positive_unsigned_data_cannot_promote(self):
        d = sample()
        d["router"].update(
            anonymous_legacy_providers_http=401,
            effective_providers_http=200,
            runtime_projection_fresh=True,
            runtime_projection_last_error_present=False,
        )
        d["assistx"].update(
            runtime_projection_http=200, mobile_runtime_count=1,
            fleet_nodes_count=1, inference_models_count=1,
        )
        d["neo4j"].update(
            approval_expires_in_minutes=10,
            current_admitted_runtimes=1,
            current_loaded_models=1,
            current_approved_paths=2,
            current_approved_capacity=1,
        )
        out = M.decide(d)
        self.assertEqual(out["status"], "NO_KNOWN_BLOCKERS_BUT_UNATTESTED")
        self.assertEqual(out["blockers"], [])
        self.assertFalse(out["can_dispatch"])
        self.assertFalse(out["complete_trusted_authority"])

    def test_incomplete_request_fails_closed(self):
        d = sample()
        del d["router"]["runtime_projection_fresh"]
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            M.decide(d)

    def test_snapshot_has_no_identity_or_raw_telemetry(self):
        text = SAMPLE.read_text()
        for prohibited in ("password", "Bearer ", "Authorization", "access_url",
                           "raw_prompt", "token_value"):
            self.assertNotIn(prohibited, text)


if __name__ == "__main__":
    unittest.main()
