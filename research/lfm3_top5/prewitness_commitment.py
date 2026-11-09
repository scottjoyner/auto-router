"""Offline author+gold-reviewer signature boundary BEFORE an RFC3161 query.

This is a two-of-three *structural* check. It does not authenticate any
human/key owner, create a trusted timestamp or authorize inference/production.
The existing witness-signed final handoff still MUST be verified separately.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .independent_custody_handoff import (
    COMMIT_SCHEMA, FROZEN_PREREG_SHA, ROLES, _sig_record,
    _trusted_roster, role_payload,
)
from .prospective_holdout_gate import MODEL_SHA, _cases_valid, _exact_manifest, canonical, sha
from .unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate

SCHEMA = 'lfm3.custody.prewitness-author-review.v1'
PRE_ROLES = ('author', 'reviewer')


def hold(reason: str) -> dict:
    return {
        'decision': 'HOLD', 'reason': reason,
        'offline_author_and_reviewer_signatures_verified': False,
        'independent_author_verified': False,
        'independent_gold_reviewer_verified': False,
        'witness_signature_verified': False,
        'trusted_key_ownership_verified': False,
        'external_timestamp_token_received': False,
        'pre_inference_seal_verified': False,
        'external_case_custody_verified': False,
        'permitted_to_infer': False,
        'model_value_verified': False,
        'production_authorized': False,
    }


def inspect_prewitness(dataset: object, package: object,
                       supplied_key_roster: object) -> dict:
    """Verify two role-separated signatures; witness must sign AFTER TSA reply.

    The third (witness) role is present only in the key roster. No witness
    signature is accepted here, and this module never creates one.
    """
    try:
        root=Path(__file__).resolve().parent
        frozen=(root/'prospective_prereg_v1.json').read_bytes()
        prereg=json.loads(frozen,object_pairs_hook=_pairs_no_duplicate,
                          parse_constant=_invalid_constant)
        if (hashlib.sha256(frozen).hexdigest()!=FROZEN_PREREG_SHA or
            not _exact_manifest(prereg)):
            return hold('frozen_preregistration_source_drift')
        if not _cases_valid(dataset):
            return hold('invalid_or_unbalanced_private_case_contract')
        roster=_trusted_roster(supplied_key_roster)
        if roster is None or set(roster)!=set(ROLES):
            return hold('missing_or_reused_three_role_public_roster')
        if type(package) is not dict or set(package)!={'schema','commitment','signatures'}:
            return hold('incorrect_or_extra_prewitness_fields')
        if package['schema']!=SCHEMA:
            return hold('wrong_prewitness_schema')
        commit=package['commitment']
        expected={
            'schema': COMMIT_SCHEMA,
            'dataset_sha256': sha(dataset),
            'prereg_sha256': FROZEN_PREREG_SHA,
            'checkpoint_sha256': MODEL_SHA,
        }
        if (type(commit) is not dict or set(commit)!=set(expected) or
            any(type(commit[k]) is not type(v) or commit[k]!=v for k,v in expected.items())):
            return hold('private_corpus_or_frozen_commitment_mismatch')
        signatures=package['signatures']
        if type(signatures) is not dict or set(signatures)!=set(PRE_ROLES):
            return hold('witness_must_sign_only_after_external_token')
        for role in PRE_ROLES:
            record=signatures[role]
            if not _sig_record(record,roster[role]['key_id']):
                return hold('untrusted_or_missing_'+role+'_signature')
            pk=Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(roster[role]['ed25519_public_key_hex']))
            try:
                pk.verify(bytes.fromhex(record['signature_hex']),
                          role_payload(role,commit,signatures,'not-used-before-tsa'))
            except InvalidSignature:
                return hold('invalid_'+role+'_signature')
        return {
            'decision':'PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY',
            'reason':'author and reviewer signatures match caller-supplied distinct keys; independent identities and TSA seal remain absent',
            'dataset_commitment_imprint_sha256':hashlib.sha256(canonical(commit).encode('utf-8')).hexdigest(),
            'offline_author_and_reviewer_signatures_verified':True,
            'independent_author_verified':False,
            'independent_gold_reviewer_verified':False,
            'witness_signature_verified':False,
            'trusted_key_ownership_verified':False,
            'external_timestamp_token_received':False,
            'pre_inference_seal_verified':False,
            'external_case_custody_verified':False,
            'permitted_to_infer':False,
            'model_value_verified':False,
            'production_authorized':False,
        }
    except (ValueError,TypeError,KeyError,OverflowError,OSError,RecursionError,UnicodeError):
        return hold('invalid_or_unavailable_prewitness_input')
