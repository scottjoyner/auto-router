#!/usr/bin/env python3
"""Opt-in, LOCAL-ONLY fictional research case projection. Never run inference.

Writes gold-free note packet + restricted custodian receipt using exclusive
0600 creation. Stdout contains counts and untrusted structural status only.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from research.lfm3_top5.blind_case_intake import DATA_CLASS, prepare_blind_packet
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_private_json_reader import read_private_json
from scripts.lfm3_private_output_writer import write_private_projection_pair


def main() -> int:
    p=argparse.ArgumentParser(description='Prepare a gold-free fictional LFM3 research packet. Never grants inference permission.')
    for name in ('dataset','bundle','key-roster','packet-output','receipt-output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--source-class',required=True,
                   help='Must be fictional_synthetic_research; no real/private inputs')
    a=p.parse_args()
    status={'decision':'HOLD','reason':'untrusted_or_unavailable_source',
            'permitted_to_infer':False,'production_authorized':False}
    try:
        if a.source_class != DATA_CLASS:
            status['reason']='unapproved_or_unproven_data_class'
        elif Path(a.packet_output).absolute() == Path(a.receipt_output).absolute():
            status['reason']='output_paths_not_distinct'
        elif any(Path(n).is_symlink() or Path(n).exists()
                 for n in (a.packet_output,a.receipt_output)):
            status['reason']='output_already_exists_no_overwrite'
        else:
            result=prepare_blind_packet(
                read_private_json(a.dataset,limit=2*1024*1024),
                read_private_json(a.bundle,limit=64*1024),
                read_private_json(a.key_roster,limit=64*1024),
                declared_data_class=a.source_class,
                blinding_secret=secrets.token_bytes(32))
            if result['decision'] != 'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY':
                status['reason']=result['reason']
            else:
                packet_json=canonical(result['packet'])+'\n'
                receipt_json=canonical(result['receipt'])+'\n'
                write_private_projection_pair(
                    a.packet_output, packet_json, a.receipt_output, receipt_json)
                status={
                    'decision':'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY',
                    'reason':'restricted local artifacts written; external custody and inference still HOLD',
                    'blind_prompt_view_sha256':result['receipt']['blind_prompt_view_sha256'],
                    'case_count':result['receipt']['case_count'],
                    'independent_author_verified':False,
                    'external_case_custody_verified':False,
                    'permitted_to_infer':False,
                    'production_authorized':False,
                }
    except (TypeError,ValueError,KeyError,OSError,UnicodeError,OverflowError,RecursionError):
        status['reason']='unreadable_or_invalid_input_or_output'
    print(canonical(status))
    return 0 if status['decision']=='PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY' else 2

if __name__=='__main__':
    raise SystemExit(main())
