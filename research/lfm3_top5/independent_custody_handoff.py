"""Offline Ed25519 handoff verifier for independently authored LFM3 evaluation.

Cryptographically valid signatures are *not* independently trusted identities,
external timestamp custody, prior case sealing, or deployment authorization.
This module cannot make those claims and performs no network operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .prospective_holdout_gate import (
    MODEL_SHA, _cases_valid, _exact_manifest, canonical, sha
)
from .unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate

SCHEMA = "lfm3.custody.three-role-handoff.v1"
KEY_SCHEMA = "lfm3.custody.trusted-keys.v1"
COMMIT_SCHEMA = "lfm3.custody.dataset-commitment.v1"
FROZEN_PREREG_SHA = "38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696"
ROLES = ("author", "reviewer", "witness")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX128 = re.compile(r"[0-9a-f]{128}")
KEY_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{7,79}")
ANCHOR = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9:./_-]{9,239}")

def hold(reason: str) -> dict:
    return {
        "decision": "HOLD",
        "reason": reason,
        "offline_signatures_valid": False,
        "independent_author_verified": False,
        "trusted_key_ownership_verified": False,
        "external_anchor_verified": False,
        "pre_inference_seal_verified": False,
        "external_custody_verified": False,
        "model_value_verified": False,
        "production_authorized": False,
    }

def role_payload(role: str, commitment: dict, signatures: dict,
                 anchor_reference: str) -> bytes:
    """Domain-separated deterministic bytes; no private keys in this module."""
    if role not in ROLES:
        raise ValueError("unsupported role")
    if role == "witness":
        statement = {
            "commitment": commitment,
            "author_signature_hex": signatures["author"]["signature_hex"],
            "reviewer_signature_hex": signatures["reviewer"]["signature_hex"],
            "claimed_external_anchor_reference": anchor_reference,
        }
    else:
        statement = {"commitment": commitment, "role": role}
    return (f"lfm3:prospective-custody:{role}:v1\n" +
            canonical(statement)).encode("utf-8")

def _trusted_roster(roster: object) -> dict | None:
    if type(roster) is not dict or set(roster) != {"schema", "roles"}:
        return None
    if roster["schema"] != KEY_SCHEMA or type(roster["roles"]) is not dict:
        return None
    records = roster["roles"]
    if set(records) != set(ROLES):
        return None
    used_ids,used_pubkeys = set(),set()
    for role in ROLES:
        item=records[role]
        if type(item) is not dict or set(item) != {"key_id","ed25519_public_key_hex"}:
            return None
        kid,pub=item["key_id"],item["ed25519_public_key_hex"]
        if (type(kid) is not str or not KEY_ID.fullmatch(kid) or kid in used_ids or
            type(pub) is not str or not HEX64.fullmatch(pub) or pub in used_pubkeys):
            return None
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub))
        used_ids.add(kid)
        used_pubkeys.add(pub)
    return records

def _sig_record(item: object, expected_key_id: str) -> bool:
    return (type(item) is dict and set(item) == {"key_id","signature_hex"}
            and type(item["key_id"]) is str and item["key_id"]==expected_key_id
            and type(item["signature_hex"]) is str
            and HEX128.fullmatch(item["signature_hex"]) is not None)

def verify_handoff(dataset: object, package: object,
                   trusted_keys: object) -> dict:
    """Structural signature validation only; external custody always false.

    Public keys MUST be authenticated out of band by an independent reviewer.
    Passing them to this function does NOT prove their authenticity.
    """
    try:
        root=Path(__file__).resolve().parent
        prereg_file=root/"prospective_prereg_v1.json"
        raw=prereg_file.read_bytes()
        prereg=json.loads(raw, object_pairs_hook=_pairs_no_duplicate,
                          parse_constant=_invalid_constant)
        if (hashlib.sha256(raw).hexdigest() != FROZEN_PREREG_SHA or
            not _exact_manifest(prereg)):
            return hold("frozen_preregistration_source_drift")
        if not _cases_valid(dataset):
            return hold("invalid_or_unbalanced_independent_case_contract")
        roster=_trusted_roster(trusted_keys)
        if roster is None:
            return hold("missing_nonoverlapping_three_role_key_roster")
        if (type(package) is not dict or set(package) != {
            "schema","commitment","signatures","claimed_external_anchor_reference"
        }):
            return hold("invalid_handoff_bundle")
        if package["schema"] != SCHEMA:
            return hold("wrong_handoff_schema")
        commitment=package["commitment"]
        expected={
            "schema":COMMIT_SCHEMA,
            "dataset_sha256":sha(dataset),
            "prereg_sha256":FROZEN_PREREG_SHA,
            "checkpoint_sha256":MODEL_SHA,
        }
        if (type(commitment) is not dict or set(commitment)!=set(expected)
            or any(type(commitment[k]) is not type(v) or commitment[k]!=v
                   for k,v in expected.items())):
            return hold("case_or_prereg_commitment_mismatch")
        sigs=package["signatures"]
        if type(sigs) is not dict or set(sigs)!=set(ROLES):
            return hold("missing_signature_or_unexpected_role")
        anchor=package["claimed_external_anchor_reference"]
        if type(anchor) is not str or not ANCHOR.fullmatch(anchor):
            return hold("missing_external_anchor_reference")
        for role in ROLES:
            if not _sig_record(sigs[role],roster[role]["key_id"]):
                return hold("untrusted_signature_record_"+role)
            pub=Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(roster[role]["ed25519_public_key_hex"]))
            try:
                pub.verify(bytes.fromhex(sigs[role]["signature_hex"]),
                           role_payload(role,commitment,sigs,anchor))
            except InvalidSignature:
                return hold("invalid_signature_"+role)
        return {
            "decision": "PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY",
            "dataset_sha256":commitment["dataset_sha256"],
            "prereg_sha256":FROZEN_PREREG_SHA,
            "separately_keyed_signature_count":3,
            "offline_signatures_valid": True,
            "independent_author_verified": False,
            "trusted_key_ownership_verified": False,
            "external_anchor_verified": False,
            "pre_inference_seal_verified": False,
            "external_custody_verified": False,
            "model_value_verified": False,
            "production_authorized": False,
            "reason": "Role signatures match supplied keys; signer identities, independent timestamps and external seals remain unverified.",
        }
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,OSError):
        return hold("untrusted_or_unavailable_custody_input")

def _read_json(path: str):
    with open(path,encoding="utf-8") as stream:
        return json.load(stream,object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)

def main() -> int:
    p=argparse.ArgumentParser(description="Offline three-role LFM3 research handoff verifier; NEVER release approval")
    p.add_argument("--dataset",required=True,help="Local sealed fictional/sanitized cases JSON")
    p.add_argument("--bundle",required=True,help="Signed local handoff bundle JSON")
    p.add_argument("--key-roster",required=True,help="Separately authenticated Ed25519 public-key roster JSON")
    args=p.parse_args()
    try:
        out=verify_handoff(_read_json(args.dataset),_read_json(args.bundle),
                           _read_json(args.key_roster))
    except (OSError,ValueError,TypeError):
        out=hold("missing_or_unreadable_handoff_file")
    print(canonical(out))
    return 0 if out["decision"]=="PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
