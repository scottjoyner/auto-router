#!/usr/bin/env python3
"""Offline author/reviewer signing payload export and returned signature assembly.

Does not generate, open, read, sign with, or manage ANY private keys.
No TSA contact, real identity proof, model inference or release admission.
"""
from __future__ import annotations

import argparse
import os

from research.lfm3_top5.signer_payload_handoff import (
    hold, prepare_unsigned_payloads, assemble_external_role_signatures,
)
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_private_json_reader import read_private_json,read_private_bytes
from scripts.lfm3_private_output_writer import (
    _target_dirfd, _absent, _owned_create, _rollback,
    write_private_projection_pair,
)


INPUT_LIMIT={'dataset':2*1024*1024,'key_roster':65536}
OUTPUT_LIMIT=16384


def _private_load(path: str, *, limit: int, binary: bool=False):
    fd=None
    try:
        fd,name=_target_dirfd(path)
        if binary:return read_private_bytes(name,limit=limit,dir_fd=fd)
        return read_private_json(name,limit=limit,dir_fd=fd)
    finally:
        if fd is not None:os.close(fd)


def _write_private_one(path: str, content: bytes):
    if type(content) is not bytes or not 0<len(content)<=OUTPUT_LIMIT:
        raise ValueError('invalid_private_handoff_output_size')
    fd=None;created=[]
    try:
        fd,name=_target_dirfd(path)
        _absent(fd,name)
        _owned_create(fd,name,content,created)
        os.fsync(fd)
    except (OSError,ValueError):
        _rollback(created)
        raise
    finally:
        if fd is not None:os.close(fd)


def main():
    p=argparse.ArgumentParser(description='Offline LFM3 detached author+reviewer handoff; no key access or inference')
    sub=p.add_subparsers(dest='operation',required=True)
    prepare=sub.add_parser('prepare',help='Export EXACT signed payload bytes, WITHOUT signing them')
    prepare.add_argument('--dataset',required=True)
    prepare.add_argument('--key-roster',required=True)
    prepare.add_argument('--author-payload',required=True)
    prepare.add_argument('--reviewer-payload',required=True)
    assemble=sub.add_parser('assemble',help='Verify external signatures and create unsigned-witness prewitness JSON')
    assemble.add_argument('--dataset',required=True)
    assemble.add_argument('--key-roster',required=True)
    assemble.add_argument('--author-signature',required=True)
    assemble.add_argument('--reviewer-signature',required=True)
    assemble.add_argument('--prewitness-output',required=True)
    args=p.parse_args()
    result=hold('missing_private_role_handoff_inputs')
    try:
        dataset=_private_load(args.dataset,limit=INPUT_LIMIT['dataset'])
        roster=_private_load(args.key_roster,limit=INPUT_LIMIT['key_roster'])
        if args.operation=='prepare':
            result,payloads=prepare_unsigned_payloads(dataset,roster)
            if result['decision']=='PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY':
                write_private_projection_pair(args.author_payload,payloads['author'].decode('utf-8'),
                                              args.reviewer_payload,payloads['reviewer'].decode('utf-8'))
        else:
            raw={
                'author':_private_load(args.author_signature,limit=64,binary=True),
                'reviewer':_private_load(args.reviewer_signature,limit=64,binary=True),
            }
            result,package=assemble_external_role_signatures(dataset,roster,raw)
            if result['decision']=='PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY':
                _write_private_one(args.prewitness_output,(canonical(package)+'\n').encode('utf-8'))
    except (OSError,TypeError,ValueError,KeyError,OverflowError,RecursionError,UnicodeError):
        result=hold('invalid_private_signer_handoff_file_or_destination')
    print(canonical(result))
    return 0 if result['decision'] in (
        'PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY',
        'PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY',
    ) else 2

if __name__=='__main__':
    raise SystemExit(main())
