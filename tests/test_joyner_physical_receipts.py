"""Immutable privacy-safe physical receipt checks, without live network access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RECEIPT_DIR = Path(__file__).resolve().parents[1] / "docs/joyner-r9700-receipts"
RECEIPTS = {
    "forward-census-45s-20261023.json":
        "7e32643764b315250deb7d88adb101e601ce68ec265154539015530cc734feb4",
    "minicpm-guarded-canary-20261023.json":
        "0c5114f9a18a0ae25cf0a1e5a4d6179652c168c3e2f8599070f97f37a727d25b",
}


def test_receipts_match_immutable_digests():
    for name, checksum in RECEIPTS.items():
        raw = (RECEIPT_DIR / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == checksum


def test_census_does_not_expose_client_ip_or_grant_drain():
    data = json.loads((RECEIPT_DIR / "forward-census-45s-20261023.json").read_text())
    assert data["samples"] == 46
    assert data["socket_read_errors"] == 0
    assert data["unique_clients_observed"] == 0
    assert data["may_disable_forward"] is False
    assert data["client_inventory_complete"] is False
    assert "client_tags" not in data


def test_synthetic_canary_does_not_expose_key_or_allow_cutover():
    raw = (RECEIPT_DIR / "minicpm-guarded-canary-20261023.json").read_text()
    for forbidden in ("Authorization:", "Bearer ", "api_key", "api-key-file"):
        assert forbidden not in raw
    data = json.loads(raw)
    assert data["verified_guarded_model_sha256_matches"]
    assert data["anonymous_guarded_model_list_status"] == 401
    assert data["authorized_guarded_model_list_status"] == 200
    assert data["legacy"]["http_status"] == data["guarded"]["http_status"] == 200
    assert data["legacy"]["text_sha256"] == data["guarded"]["text_sha256"]
    assert not data["candidate_model_aliases_match"]
    assert not data["admission"]["functional_canary_gates_passed"]
    assert data["admission"]["can_cutover"] is False
