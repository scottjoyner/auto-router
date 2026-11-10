"""Prepare exact Ed25519 signing bytes for external author and gold reviewer.

Pure and offline. NO key generation, raw case disclosure, TSA request, inferred
identity, model requests, or deployment permission. A supplied three-key
roster cannot establish that its human owners are genuinely independent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .independent_custody_handoff import (
    COMMIT_SCHEMA, FROZEN_PREREG_SHA, ROLES, _trusted_roster, role_payload,
)
from .prewitness_commitment import SCHEMA as PREWITNESS_SCHEMA, inspect_prewitness
from .prospective_holdout_gate import (
    MODEL_SHA, _cases_valid, _exact_manifest, sha,
)
from .unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate

SIGN_ROLES = ('author', 'reviewer')
PAYLOAD_LIMIT = 4096


def hold(reason: str) -> dict:
    return {
        'decision': 'HOLD', 'reason': reason,
        'signing_payloads_prepared': False,
        'offline_signatures_verified': False,
        'independent_author_verified': False,
        'independent_gold_reviewer_verified': False,
        'key_ownership_independently_authenticated': False,
        'witness_signature_verified': False,
        'external_timestamp_token_received': False,
        'pre_inference_seal_verified': False,
        'external_case_custody_verified': False,
        'permitted_to_infer': False,
        'model_value_verified': False,
        'production_authorized': False,
    }


def _commit_for_valid_private_data(dataset: object, roster: object):
    frozen=(Path(__file__).resolve().parent/'prospective_prereg_v1.json').read_bytes()
    manifest=json.loads(frozen,object_pairs_hook=_pairs_no_duplicate,
                        parse_constant=_invalid_constant)
    if (hashlib.sha256(frozen).hexdigest()!=FROZEN_PREREG_SHA or
        not _exact_manifest(manifest)):
        return None,None,'frozen_preregistration_source_drift'
    if not _cases_valid(dataset):
        return None,None,'unbalanced_or_invalid_signed_case_set'
    identities=_trusted_roster(roster)
    if identities is None or set(identities)!=set(ROLES):
        return None,None,'missing_or_reused_public_key_roster'
    commitment={
        'schema': COMMIT_SCHEMA,
        'dataset_sha256': sha(dataset),
        'prereg_sha256': FROZEN_PREREG_SHA,
        'checkpoint_sha256': MODEL_SHA,
    }
    return commitment,identities,None


def _payloads(commitment: dict) -> dict:
    return {role:role_payload(role,commitment,{},'unused-before-tsa')
            for role in SIGN_ROLES}


def prepare_unsigned_payloads(dataset: object, roster: object) -> tuple[dict,dict | None]:
    """Role-specific EXACT payload bytes; user must not upload raw cases/gold."""
    try:
        commitment,identities,reason=_commit_for_valid_private_data(dataset,roster)
        if reason:return hold(reason),None
        payloads=_payloads(commitment)
        if any(not 0<len(value)<=PAYLOAD_LIMIT for value in payloads.values()):
            return hold('invalid_or_oversized_signing_payload'),None
        out={
            'decision':'PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY',
            'reason':'exact role-separated signing bytes; no human key ownership or source custody verified',
            'payload_sha256':{role:hashlib.sha256(payloads[role]).hexdigest()
                              for role in SIGN_ROLES},
            'signing_payloads_prepared':True,
            'offline_signatures_verified':False,
            'independent_author_verified':False,
            'independent_gold_reviewer_verified':False,
            'key_ownership_independently_authenticated':False,
            'witness_signature_verified':False,
            'external_timestamp_token_received':False,
            'pre_inference_seal_verified':False,
            'external_case_custody_verified':False,
            'permitted_to_infer':False,
            'model_value_verified':False,
            'production_authorized':False,
        }
        return out,payloads
    except (TypeError,ValueError,KeyError,OverflowError,OSError,
            RecursionError,UnicodeError):
        return hold('untrusted_or_unavailable_offline_signing_inputs'),None


def verify_unsigned_role_payload(dataset: object, roster: object,
                                 role: object, supplied_payload: object) -> dict:
    """Signer-side exact-byte verification before anyone signs anything.

    The caller must independently review/authenticate the entire private
    dataset and original public-key roster. This only compares its local copy
    to a role-specific payload; no individual identity or custody is proved.
    """
    try:
        if type(role) is not str or role not in SIGN_ROLES:
            return hold('unknown_or_witness_role_not_permitted_before_tsa')
        if (type(supplied_payload) is not bytes or
            not 0 < len(supplied_payload) <= PAYLOAD_LIMIT):
            return hold('invalid_detached_signing_payload_bytes')
        prepared, payloads=prepare_unsigned_payloads(dataset,roster)
        if prepared['decision']!='PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY':
            return hold('unverifiable_original_role_payload_'+prepared['reason'])
        expected=payloads[role]
        if not hmac.compare_digest(expected,supplied_payload):
            return hold('payload_does_not_match_private_reviewed_case_commitment')
        return {
            'decision':'PASS_STATIC_SIGNER_PAYLOAD_MATCH_ONLY',
            'reason':'exact role payload matches caller-supplied dataset and frozen commitment; no external signer authentication',
            'role':role,
            'payload_sha256':hashlib.sha256(expected).hexdigest(),
            'signing_payloads_prepared':True,
            'offline_signatures_verified':False,
            'payload_matches_supplied_dataset':True,
            'independent_dataset_review_verified':False,
            'independent_author_verified':False,
            'independent_gold_reviewer_verified':False,
            'key_ownership_independently_authenticated':False,
            'witness_signature_verified':False,
            'external_timestamp_token_received':False,
            'pre_inference_seal_verified':False,
            'external_case_custody_verified':False,
            'permitted_to_infer':False,
            'model_value_verified':False,
            'production_authorized':False,
        }
    except (TypeError,ValueError,KeyError,OverflowError,OSError,
            RecursionError,UnicodeError):
        return hold('untrusted_or_unavailable_signer_payload')


def assemble_external_role_signatures(dataset: object, roster: object,
                                      detached_signatures: object) -> tuple[dict,dict | None]:
    """Accept raw 64-byte Ed25519 signatures, no private key read/access.

    Success is mathematical verification against supplied roster ONLY. The
    exported prewitness bundle still needs real independent trust and a TSA.
    """
    try:
        commitment,identities,reason=_commit_for_valid_private_data(dataset,roster)
        if reason:return hold(reason),None
        if (type(detached_signatures) is not dict or
            set(detached_signatures)!=set(SIGN_ROLES) or
            any(type(detached_signatures[role]) is not bytes or
                len(detached_signatures[role])!=64 for role in SIGN_ROLES)):
            return hold('missing_or_malformed_detached_role_signatures'),None
        unsigned=_payloads(commitment)
        signatures={}
        for role in SIGN_ROLES:
            pk=Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(identities[role]['ed25519_public_key_hex']))
            try:
                pk.verify(detached_signatures[role],unsigned[role])
            except InvalidSignature:
                return hold('invalid_or_misattributed_'+role+'_signature'),None
            signatures[role]={
                'key_id':identities[role]['key_id'],
                'signature_hex':detached_signatures[role].hex(),
            }
        package={'schema':PREWITNESS_SCHEMA,
                 'commitment':commitment,'signatures':signatures}
        existing=inspect_prewitness(dataset,package,roster)
        if existing['decision']!='PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY':
            return hold('unacceptable_prewitness_bundle_'+existing['reason']),None
        out={
            'decision':'PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY',
            'reason':'external detached author/reviewer signatures match caller-supplied keys; no independent human identity proved',
            'prewitness_bundle_sha256':sha(package),
            'signing_payloads_prepared':True,
            'offline_signatures_verified':True,
            'independent_author_verified':False,
            'independent_gold_reviewer_verified':False,
            'key_ownership_independently_authenticated':False,
            'witness_signature_verified':False,
            'external_timestamp_token_received':False,
            'pre_inference_seal_verified':False,
            'external_case_custody_verified':False,
            'permitted_to_infer':False,
            'model_value_verified':False,
            'production_authorized':False,
        }
        return out,package
    except (TypeError,ValueError,KeyError,OverflowError,OSError,
            RecursionError,UnicodeError):
        return hold('untrusted_or_unavailable_offline_detached_signatures'),None
