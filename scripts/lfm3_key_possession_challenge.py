#!/usr/bin/env python3
"""Offline Ed25519 role key-control challenge. Never accesses private keys."""
from __future__ import annotations

import argparse
import os

from research.lfm3_top5.key_possession_contract import (
    issue_local_challenge, challenge_signing_bytes,
    verify_detached_key_possession, hold,
)
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_private_json_reader import read_private_json, read_private_bytes
from scripts.lfm3_private_output_writer import (
    _target_dirfd, write_private_projection_pair,
)


def _read(path: str, size: int, binary: bool=False):
    fd=None
    try:
        fd,name=_target_dirfd(path)
        return (read_private_bytes(name,limit=size,dir_fd=fd) if binary
                else read_private_json(name,limit=size,dir_fd=fd))
    finally:
        if fd is not None:os.close(fd)


def main() -> int:
    p=argparse.ArgumentParser(description='Offline key possession structure, not independent signer identification')
    sub=p.add_subparsers(dest='action',required=True)
    prepare=sub.add_parser('prepare',help='Create a local nonce challenge and exact detached signing bytes')
    prepare.add_argument('--key-roster',required=True)
    prepare.add_argument('--role',required=True,choices=('author','reviewer','witness'))
    prepare.add_argument('--challenge-output',required=True)
    prepare.add_argument('--payload-output',required=True)
    verify=sub.add_parser('verify',help='Check detached raw Ed25519 role challenge signature')
    verify.add_argument('--key-roster',required=True)
    verify.add_argument('--role',required=True,choices=('author','reviewer','witness'))
    verify.add_argument('--challenge',required=True)
    verify.add_argument('--signature',required=True)
    a=p.parse_args()
    result=hold('unavailable_independent_enrollment_evidence')
    try:
        roster=_read(a.key_roster,65536)
        if a.action=='prepare':
            result,challenge=issue_local_challenge(roster,a.role)
            if challenge is not None and result['decision']=='PASS_OFFLINE_CHALLENGE_CREATED_ONLY':
                checked,signing_bytes=challenge_signing_bytes(roster,a.role,challenge)
                if signing_bytes is None:
                    result=hold('unable_to_derive_role_signing_bytes')
                else:
                    write_private_projection_pair(
                        a.challenge_output,canonical(challenge)+'\n',
                        a.payload_output,signing_bytes.decode('utf-8'))
        else:
            challenge=_read(a.challenge,8192)
            sig=_read(a.signature,64,binary=True)
            result=verify_detached_key_possession(roster,a.role,challenge,sig)
    except (TypeError,ValueError,KeyError,OSError,RecursionError,OverflowError,UnicodeError):
        result=hold('untrusted_or_unsafe_offline_enrollment_file')
    print(canonical(result))
    return 0 if result['decision'] in (
        'PASS_OFFLINE_CHALLENGE_CREATED_ONLY',
        'PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY',
    ) else 2

if __name__=='__main__':
    raise SystemExit(main())
