#!/usr/bin/env python3
"""Read-only offline private pair check; never prints gold, mapping or notes."""
from __future__ import annotations

import argparse
import os

from research.lfm3_top5.private_pair_integrity import hold, inspect_private_pair
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_private_json_reader import read_private_json
from scripts.lfm3_private_output_writer import _target_dirfd, PACKET_MAX_BYTES, RECEIPT_MAX_BYTES


def audit_private_paths(packet_path: str, receipt_path: str) -> dict:
    pfd=None;rfd=None
    try:
        # No symlink ancestor, and both final directories 0700 and owner-only.
        pfd,pname=_target_dirfd(packet_path)
        rfd,rname=_target_dirfd(receipt_path)
        a=os.fstat(pfd);b=os.fstat(rfd)
        if (a.st_dev,a.st_ino,pname)==(b.st_dev,b.st_ino,rname):
            return hold('same_file_used_as_packet_and_receipt')
        packet=read_private_json(pname,limit=PACKET_MAX_BYTES,dir_fd=pfd)
        receipt=read_private_json(rname,limit=RECEIPT_MAX_BYTES,dir_fd=rfd)
        return inspect_private_pair(packet,receipt)
    except (ValueError,OSError,TypeError,UnicodeError,RecursionError,OverflowError):
        return hold('missing_unsafe_or_unreadable_private_pair')
    finally:
        if pfd is not None:os.close(pfd)
        if rfd is not None:os.close(rfd)


def main() -> int:
    p=argparse.ArgumentParser(description='Verify offline local private packet/receipt pair linkage; NEVER approves inference')
    p.add_argument('--packet',required=True)
    p.add_argument('--receipt',required=True)
    a=p.parse_args()
    result=audit_private_paths(a.packet,a.receipt)
    print(canonical(result))
    return 0 if result['decision']=='PASS_LOCAL_PAIR_LINKAGE_ONLY' else 2

if __name__=='__main__':
    raise SystemExit(main())
