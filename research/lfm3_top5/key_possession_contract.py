"""Offline per-role Ed25519 proof-of-possession; NOT identity enrollment.

The independent verifier must issue/record a fresh challenge and separately
verify the signer's real identity. No network, key generation, TSA, or inference.
"""
from __future__ import annotations

import hashlib
import secrets
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .independent_custody_handoff import ROLES, FROZEN_PREREG_SHA, _trusted_roster
from .prospective_holdout_gate import MODEL_SHA, canonical, sha

SCHEMA = 'lfm3.custody.key-possession-challenge.v1'
PURPOSE = 'ed25519_key_control_only_not_human_identity'
DOMAIN = b'lfm3:custody:key-possession:v1\n'
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
FIELDS = frozenset({'schema', 'purpose', 'role', 'key_id',
                    'ed25519_public_key_sha256', 'nonce_hex',
                    'prereg_sha256', 'checkpoint_sha256',
                    'independent_identity_verified', 'permitted_to_infer',
                    'production_authorized'})


def hold(reason: str) -> dict:
    return {
        'decision': 'HOLD', 'reason': reason,
        'challenge_shape_verified': False,
        'signature_matches_supplied_public_key': False,
        'independent_challenge_issuance_verified': False,
        'challenge_freshness_and_single_use_verified': False,
        'independent_human_identity_verified': False,
        'signer_organizational_independence_verified': False,
        'independent_case_custody_verified': False,
        'pre_inference_seal_verified': False,
        'permitted_to_infer': False,
        'model_value_verified': False,
        'production_authorized': False,
    }


def _role_record(roster: object, role: object):
    if type(role) is not str or role not in ROLES:
        return None
    records = _trusted_roster(roster)
    return records[role] if records is not None else None


def _challenge_matches(challenge: object, record: dict, role: str) -> bool:
    if type(challenge) is not dict or set(challenge) != FIELDS:
        return False
    pub = bytes.fromhex(record['ed25519_public_key_hex'])
    return (
        type(challenge['schema']) is str and challenge['schema'] == SCHEMA and
        type(challenge['purpose']) is str and challenge['purpose'] == PURPOSE and
        type(challenge['role']) is str and challenge['role'] == role and
        type(challenge['key_id']) is str and challenge['key_id'] == record['key_id'] and
        type(challenge['ed25519_public_key_sha256']) is str and
        challenge['ed25519_public_key_sha256'] == hashlib.sha256(pub).hexdigest() and
        type(challenge['nonce_hex']) is str and HEX64.fullmatch(challenge['nonce_hex']) is not None and
        type(challenge['prereg_sha256']) is str and challenge['prereg_sha256'] == FROZEN_PREREG_SHA and
        type(challenge['checkpoint_sha256']) is str and challenge['checkpoint_sha256'] == MODEL_SHA and
        challenge['independent_identity_verified'] is False and
        challenge['permitted_to_infer'] is False and
        challenge['production_authorized'] is False
    )


def issue_local_challenge(roster: object, role: object) -> tuple[dict, dict | None]:
    """Generate fresh local random nonce; issuer identity remains unproven."""
    try:
        record = _role_record(roster, role)
        if record is None:
            return hold('invalid_role_or_untrusted_distinct_public_roster'), None
        challenge = {
            'schema': SCHEMA,
            'purpose': PURPOSE,
            'role': role,
            'key_id': record['key_id'],
            'ed25519_public_key_sha256': hashlib.sha256(
                bytes.fromhex(record['ed25519_public_key_hex'])).hexdigest(),
            'nonce_hex': secrets.token_hex(32),
            'prereg_sha256': FROZEN_PREREG_SHA,
            'checkpoint_sha256': MODEL_SHA,
            'independent_identity_verified': False,
            'permitted_to_infer': False,
            'production_authorized': False,
        }
        if not _challenge_matches(challenge, record, role):
            return hold('generated_challenge_does_not_match_contract'), None
        return {
            **hold('offline_nonce_challenge_only'),
            'decision': 'PASS_OFFLINE_CHALLENGE_CREATED_ONLY',
            'challenge_shape_verified': True,
            'challenge_sha256': sha(challenge),
            'role': role,
        }, challenge
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return hold('unavailable_or_invalid_challenge_issuer_inputs'), None


def challenge_signing_bytes(roster: object, role: object, challenge: object) -> tuple[dict, bytes | None]:
    """Exact bytes to sign, no signer key required or accessed."""
    try:
        record = _role_record(roster, role)
        if record is None or not _challenge_matches(challenge, record, role):
            return hold('challenge_does_not_match_pinned_role_and_public_key'), None
        payload = DOMAIN + canonical(challenge).encode('utf-8')
        return {
            **hold('offline_role_challenge_bytes_only'),
            'decision': 'PASS_STATIC_KEY_CHALLENGE_BYTES_ONLY',
            'challenge_shape_verified': True,
            'challenge_sha256': sha(challenge),
            'payload_sha256': hashlib.sha256(payload).hexdigest(),
            'role': role,
        }, payload
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, UnicodeError):
        return hold('invalid_role_or_challenge_bytes'), None


def verify_detached_key_possession(roster: object, role: object,
                                   challenge: object, signature: object) -> dict:
    """Checks one 64-byte signature; self-issued challenge is never trusted time."""
    try:
        status, payload = challenge_signing_bytes(roster, role, challenge)
        if payload is None:
            return hold('invalid_key_control_challenge_'+status['reason'])
        if type(signature) is not bytes or len(signature) != 64:
            return hold('invalid_raw_ed25519_signature_length')
        record = _role_record(roster, role)
        key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(record['ed25519_public_key_hex']))
        try:
            key.verify(signature, payload)
        except InvalidSignature:
            return hold('signature_does_not_match_supplied_public_role_key')
        return {
            **hold('signature validates against caller-provided public key only; independence and fresh human issuance unverified'),
            'decision': 'PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY',
            'challenge_shape_verified': True,
            'signature_matches_supplied_public_key': True,
            'challenge_sha256': sha(challenge),
            'role': role,
        }
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, UnicodeError):
        return hold('malformed_or_unverifiable_possession_response')
