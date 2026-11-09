"""Offline inspection of a gold-free packet + private alias receipt pair.

PASS means only that both locally supplied objects are mutually consistent.
A coherent forgery, an unfinished but fully written pair, or dishonest human
custody cannot be ruled out. This NEVER allows inference or production.
"""
from __future__ import annotations

import re

from .blind_case_intake import DATA_CLASS, GOLD_HINT, SCHEMA, RECEIPT_SCHEMA
from .blind_capture_reconciliation import PACKET_KEYS, RECEIPT_KEYS, ALIASES
from .prospective_holdout_gate import MIN_CASES, MODEL_SHA, CODES, sha

CASE_ID=re.compile(r'[A-Z][A-Z0-9_-]{2,39}')
HEX64=re.compile(r'[0-9a-f]{64}')
REASON_VALID='present files and mutually consistent shapes/hashes only; external custody unverified'


def hold(reason: str) -> dict:
    return {
        'decision':'HOLD','reason':reason,
        'private_pair_structure_verified':False,
        'export_transaction_completion_verified':False,
        'original_signed_dataset_verified':False,
        'trusted_key_ownership_verified':False,
        'external_case_custody_verified':False,
        'pre_inference_seal_verified':False,
        'actual_model_execution_verified':False,
        'model_value_verified':False,
        'permitted_to_infer':False,
        'production_authorized':False,
    }


def inspect_private_pair(packet: object, receipt: object) -> dict:
    """Pure bounded-shape examination; never echoes private notes or IDs."""
    try:
        if type(packet) is not dict or set(packet)!=PACKET_KEYS or packet['schema']!=SCHEMA:
            return hold('missing_or_unexpected_blind_packet_contract')
        if (packet['checkpoint_sha256']!=MODEL_SHA or
            packet['data_class_self_declared']!=DATA_CLASS or
            type(packet['case_count']) is not int or
            any(packet[k] is not False for k in (
                'external_custody_verified','permitted_to_infer','production_authorized'))):
            return hold('unapproved_packet_identity_or_authority')
        count=packet['case_count'];rows=packet['cases']
        if (not MIN_CASES<=count<=250 or type(rows) is not list or len(rows)!=count):
            return hold('missing_or_imbalanced_packet_case_rows')
        ids=[];notes=set()
        for row in rows:
            if type(row) is not dict or set(row)!={'id','note'}:
                return hold('unexpected_gold_or_extra_model_facing_field')
            alias,note=row['id'],row['note']
            if (type(alias) is not str or ALIASES.fullmatch(alias) is None or
                type(note) is not str or not 80<=len(note)<=4000 or
                note in notes):
                return hold('untrusted_alias_or_note_shape')
            if GOLD_HINT.search(note) or any(
                re.search(r'(?i)(?<!\w)'+re.escape(code)+r'(?!\w)',note)
                for code in CODES):
                return hold('obvious_gold_hint_in_model_facing_note')
            ids.append(alias);notes.add(note)
        if ids!=sorted(ids) or len(set(ids))!=count:
            return hold('missing_or_repeated_opaque_alias')
        if type(receipt) is not dict or set(receipt)!=RECEIPT_KEYS or receipt['schema']!=RECEIPT_SCHEMA:
            return hold('missing_or_unexpected_custodian_receipt_contract')
        if (type(receipt['source_dataset_sha256']) is not str or
            HEX64.fullmatch(receipt['source_dataset_sha256']) is None or
            type(receipt['blind_prompt_view_sha256']) is not str or
            HEX64.fullmatch(receipt['blind_prompt_view_sha256']) is None or
            receipt['blind_prompt_view_sha256']!=sha(packet) or
            type(receipt['case_count']) is not int or receipt['case_count']!=count or
            receipt['offline_three_role_signature_structure_verified'] is not True or
            receipt['gold_keys_omitted'] is not True or
            any(receipt[k] is not False for k in (
                'independent_author_verified','trusted_key_ownership_verified',
                'external_case_custody_verified','pre_inference_seal_verified',
                'permitted_to_infer','model_value_verified','production_authorized'))):
            return hold('private_receipt_linkage_or_authority_mismatch')
        mapping=receipt['opaque_id_to_original_id']
        if (type(mapping) is not dict or len(mapping)!=count or
            set(mapping)!=set(ids) or
            any(type(v) is not str or CASE_ID.fullmatch(v) is None
                for v in mapping.values()) or
            len(set(mapping.values()))!=count):
            return hold('missing_or_repeated_private_original_ids')
        return {
            'decision':'PASS_LOCAL_PAIR_LINKAGE_ONLY','reason':REASON_VALID,
            'case_count':count,'blind_prompt_view_sha256':sha(packet),
            'private_pair_structure_verified':True,
            'export_transaction_completion_verified':False,
            'original_signed_dataset_verified':False,
            'trusted_key_ownership_verified':False,
            'external_case_custody_verified':False,
            'pre_inference_seal_verified':False,
            'actual_model_execution_verified':False,
            'model_value_verified':False,
            'permitted_to_infer':False,'production_authorized':False,
        }
    except (TypeError,ValueError,KeyError,OverflowError,OSError,
            RecursionError,UnicodeError):
        return hold('malformed_or_unverifiable_private_pair')
