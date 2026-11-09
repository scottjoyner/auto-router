#!/usr/bin/env python3
"""Offline RFC3161 query for a structurally verified two-role case commitment.

No network/TSA submission, no signer generation, no model inference.
Writes only one 0600 DER TSQ under the private custodian's 0700 directory.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from research.lfm3_top5.prewitness_commitment import hold, inspect_prewitness
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_private_json_reader import read_private_json
from scripts.lfm3_private_output_writer import (
    _target_dirfd, _absent, _owned_create, _rollback,
)
from scripts.lfm3_rfc3161_anchor_verify import (
    OPENSSL, MAX_DER, _query_imprint_is_expected, _single_der_sequence,
)

SOURCE_INPUT_CAPS = (("dataset",2*1024*1024),('prewitness',65536),('key_roster',65536))


def prepare_prewitness_query(dataset: object, prewitness: object,
                             key_roster: object) -> tuple[dict, bytes | None]:
    """Uses locally installed OpenSSL to make an unsigned nonce-bearing TSQ."""
    verdict=inspect_prewitness(dataset,prewitness,key_roster)
    if verdict['decision']!='PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY':
        return verdict,None
    digest=verdict['dataset_commitment_imprint_sha256']
    try:
        with tempfile.TemporaryDirectory(prefix='lfm3-prewitness-tsq-') as tmp:
            outfile=Path(tmp)/'nonce-request.tsq'
            args=[OPENSSL,'ts','-query','-sha256','-digest',digest,
                  '-cert','-out',str(outfile)]
            made=subprocess.run(args,capture_output=True,text=True,timeout=12,
                                check=False,stdin=subprocess.DEVNULL)
            if made.returncode!=0:
                return hold('openssl_timestamp_query_generation_unavailable'),None
            query=outfile.read_bytes()
            if not _single_der_sequence(query) or len(query)>MAX_DER:
                return hold('invalid_generated_timestamp_query_der'),None
            looked=subprocess.run([OPENSSL,'ts','-query','-in',str(outfile),'-text'],
                                  capture_output=True,text=True,timeout=12,check=False,
                                  stdin=subprocess.DEVNULL)
            if looked.returncode!=0 or not _query_imprint_is_expected(looked.stdout,digest):
                return hold('query_nonce_or_commitment_imprint_missing'),None
            result={
                'decision':'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY',
                'reason':'local RFC3161 nonce-bearing query for signed case commitment; no TSA sent or trusted custody established',
                'timestamp_query_sha256':hashlib.sha256(query).hexdigest(),
                'query_bytes':len(query),
                'two_role_crypto_structure_verified':True,
                'query_nonce_present':True,
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
            return result,query
    except (OSError,subprocess.TimeoutExpired,ValueError,TypeError,OverflowError):
        return hold('unavailable_local_openssl_query_operation'),None


def read_private_input(path: str,limit: int) -> object:
    dfd=None
    try:
        dfd,name=_target_dirfd(path)
        return read_private_json(name,limit=limit,dir_fd=dfd)
    finally:
        if dfd is not None:os.close(dfd)


def write_single_approved_private_query(path: str,query: bytes) -> None:
    if type(query) is not bytes or not 0<len(query)<=MAX_DER or not _single_der_sequence(query):
        raise ValueError('unsafe_or_oversized_query_bytes')
    fd=None;created=[]
    try:
        fd,name=_target_dirfd(path)
        _absent(fd,name)
        _owned_create(fd,name,query,created)
        os.fsync(fd)
    except (OSError,ValueError):
        _rollback(created)
        raise
    finally:
        if fd is not None:os.close(fd)


def main() -> int:
    p=argparse.ArgumentParser(description='Prepare local RFC3161 query from signed fictional cases; no external TSA or inference')
    p.add_argument('--dataset',required=True)
    p.add_argument('--prewitness',required=True)
    p.add_argument('--key-roster',required=True)
    p.add_argument('--tsq-output',required=True)
    args=p.parse_args()
    status=hold('incomplete_or_unavailable_prewitness_source')
    try:
        data={key:read_private_input(getattr(args,key),limit)
              for key,limit in SOURCE_INPUT_CAPS}
        status,query=prepare_prewitness_query(data['dataset'],data['prewitness'],data['key_roster'])
        if status['decision']=='PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY' and query is not None:
            write_single_approved_private_query(args.tsq_output,query)
    except (OSError,TypeError,ValueError,KeyError,RecursionError,UnicodeError):
        status=hold('unsafe_input_or_private_query_destination')
    # Never prints the dataset hash, private data, source IDs, gold or raw TSQ.
    print(canonical(status))
    return 0 if status['decision']=='PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY' else 2

if __name__=='__main__':
    raise SystemExit(main())
