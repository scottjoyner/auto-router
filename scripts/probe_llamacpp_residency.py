#!/usr/bin/env python3
"""Single URL, read-only, sanitized llama.cpp runtime evidence collector."""
from __future__ import annotations

import argparse
import json

from auto_router.llamacpp_residency_evidence import probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True,
                        help="Numeric private IP or localhost URL with explicit port")
    parser.add_argument("--expected-model", default="")
    args = parser.parse_args()
    try:
        evidence = probe(args.url, expected_model=args.expected_model)
    except ValueError as exc:
        print(json.dumps({"status": "hold-invalid-endpoint", "reason": str(exc),
                          "authorizes_dispatch": False}, sort_keys=True))
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0  # report-only: exit 0 does not mean admission or readiness


if __name__ == "__main__":
    raise SystemExit(main())
