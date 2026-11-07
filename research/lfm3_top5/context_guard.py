"""Bounded synthetic SensorGraft context; real signature verification is external.

Callers MUST use an independently verified SGNC relying-party verdict. A record's
signature_checked_upstream boolean is a test-interface precondition, not proof.
"""
import copy
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Context:
    text: str
    selected: tuple[str, ...]
    excluded: dict[str, int]
    consumed_characters: int


def build_context(rows, *, max_chars=1600, allowed_classes=("public_synthetic",)) -> Context:
    """Fail closed on collisions BEFORE selecting records, regardless of input order."""
    if type(max_chars) is not int or not 1 <= max_chars <= 100_000:
        raise ValueError("invalid_budget")
    if not isinstance(allowed_classes, (list, tuple, frozenset, set)):
        raise ValueError("invalid_allowed_classes")
    exclusions: dict[str, int] = {}

    def skip(reason):
        exclusions[reason] = exclusions.get(reason, 0) + 1

    valid = []
    for row in rows:
        if not isinstance(row, dict):
            skip("malformed")
            continue
        tid, sid, content = (row.get("trace_id"), row.get("source_id"),
                             row.get("content"))
        digest = row.get("content_sha256")
        if any(type(x) is not str or not x.strip() or len(x) > 256 for x in (tid, sid)):
            skip("missing_identity")
            continue
        if row.get("classification") not in allowed_classes:
            skip("forbidden_class")
            continue
        if not isinstance(content, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            skip("invalid_payload")
            continue
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            skip("digest_mismatch")
            continue
        # Precondition only; actual Ed25519 verification is outside this module.
        if row.get("signature_checked_upstream") is not True:
            skip("unverified_signature")
            continue
        valid.append((tid, sid, content, digest))

    by_source = defaultdict(set)
    by_trace = defaultdict(set)
    for tid, sid, content, digest in valid:
        by_source[sid].add(digest)
        by_trace[tid].add((sid, digest))

    accepted = []
    seen = set()
    for tid, sid, content, digest in sorted(valid, key=lambda r: (r[0], r[1], r[3])):
        if len(by_source[sid]) > 1 or len(by_trace[tid]) > 1:
            skip("ambiguous_source_identity")
            continue
        if tid in seen:
            skip("duplicate_id")
            continue
        seen.add(tid)
        accepted.append((tid, sid, content, digest))

    segments = []
    selected = []
    total = 0
    for tid, sid, content, digest in accepted:
        line = json.dumps({
            "trace_id": tid, "source_id": sid,
            "untrusted_source_text": content, "sha256": digest,
        }, ensure_ascii=True, separators=(",", ":"))
        required = len(line) + (1 if segments else 0)
        if total + required > max_chars:
            skip("budget_exhausted")
            continue
        segments.append(line)
        selected.append(tid)
        total += required

    result = "\n".join(segments)
    assert total == len(result)
    return Context(result, tuple(selected), exclusions, total)


def build_verified_context(rows, *, verifier, max_chars=1600, allowed_classes=("public_synthetic",)) -> Context:
    """Run independent verifier verdicts, then delegate selection to build_context.

    Fail closed: only a literal boolean True from the caller-supplied ``verifier``
    accepts a record. Anything else (False, None, truthy non-boolean, or an
    exception) rejects the record. The preexisting ``signature_checked_upstream``
    flag is never trusted; it is overridden to True only after an independent
    literal-True verdict.

    The verifier must be callable; otherwise ``ValueError("invalid_verifier")`` is
    raised. Each record handed to the verifier is a deep defensive copy, so caller
    mutations cannot reach the source row and the original input is never mutated.
    """
    if not callable(verifier):
        raise ValueError("invalid_verifier")
    verified_rows = []
    for row in rows:
        if not isinstance(row, dict):
            # Malformed records are left to build_context's fail-closed handling.
            verified_rows.append(row)
            continue
        try:
            candidate = copy.deepcopy(row)
            verdict = verifier(candidate)
        except Exception:
            continue
        if verdict is not True:
            continue
        # Discard verifier-side mutations. The verdict applies to the original
        # byte-level record, not a transformed or rewritten candidate.
        accepted = copy.deepcopy(row)
        accepted["signature_checked_upstream"] = True
        verified_rows.append(accepted)
    return build_context(verified_rows, max_chars=max_chars, allowed_classes=allowed_classes)
