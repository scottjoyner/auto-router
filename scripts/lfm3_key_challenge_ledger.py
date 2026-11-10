#!/usr/bin/env python3
"""Opt-in local nonce issue/consume; never identity enrollment or model access."""
from __future__ import annotations

import argparse

from research.lfm3_top5.key_challenge_ledger import (
    hold, issue_to_private_ledger, consume_once,
)
from research.lfm3_top5.prospective_holdout_gate import canonical
from scripts.lfm3_key_possession_challenge import _read


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifier-controlled LOCAL one-time challenge. Independent identity and issuance remain HOLD.")
    subs = parser.add_subparsers(dest="action", required=True)
    issue = subs.add_parser("issue")
    issue.add_argument("--key-roster", required=True)
    issue.add_argument("--role", choices=("author", "reviewer", "witness"), required=True)
    issue.add_argument("--ledger-dir", required=True)
    issue.add_argument("--challenge-output", required=True)
    issue.add_argument("--payload-output", required=True)
    consume = subs.add_parser("consume")
    consume.add_argument("--key-roster", required=True)
    consume.add_argument("--role", choices=("author", "reviewer", "witness"), required=True)
    consume.add_argument("--ledger-dir", required=True)
    consume.add_argument("--challenge", required=True)
    consume.add_argument("--signature", required=True)
    args = parser.parse_args()
    status = hold("unverified_local_key_enrollment")
    try:
        roster = _read(args.key_roster, 65536)
        if args.action == "issue":
            status = issue_to_private_ledger(
                roster, args.role, args.ledger_dir,
                args.challenge_output, args.payload_output)
        else:
            challenge = _read(args.challenge, 8192)
            raw_signature = _read(args.signature, 64, binary=True)
            status = consume_once(roster, args.role, challenge,
                                  raw_signature, args.ledger_dir)
    except (OSError, ValueError, TypeError, KeyError,
            OverflowError, RecursionError, UnicodeError):
        status = hold("unsafe_or_missing_private_local_enrollment_input")
    print(canonical(status))
    return 0 if status["decision"] in (
        "PASS_OFFLINE_LOCAL_CHALLENGE_ISSUED_ONLY",
        "PASS_OFFLINE_LOCAL_ONE_TIME_KEY_CLAIM_ONLY",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
