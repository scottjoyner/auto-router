"""Frozen v1 *public-development-only* rule baseline; never a release authority.

Do NOT tune against the sealed held-out corpus. The six previously published
synthetic narratives are development data, not held-out observations.
"""
from __future__ import annotations
import re

CODES = ("tls_sni_missing", "node_auth_contract", "gpu_attestation_missing",
         "beta_build_unlinked", "database_service_down", "insufficient_evidence")
_MARKER = re.compile(r"(?<![\w])(?P<id>E[1-9])(?:\s*\((?P<header>[^)]{0,90})\))?\s*:\s*", re.I)
_PATTERNS = {
    "tls_sni_missing": (r"tlsv1_unrecognized_name|unrecognized.name", r"sni|hostname|server.name|vhost"),
    "node_auth_contract": (r"\b401\b|unauthori[sz]ed", r"node|agent|task", r"basic|identity|credential|registered"),
    "gpu_attestation_missing": (r"gpu|tensor|kernel|dispatch", r"attest|witness|proof", r"missing|without|no\s+|not\s+|unverified|lack"),
    "beta_build_unlinked": (r"beta|testflight|testing.group", r"no linked|unlinked|not assigned|no build|without.{0,15}build"),
    "database_service_down": (r"postgres|database|5432", r"no.{0,20}listen|service.{0,15}fail|\bdown\b|refused|stopped"),
}
_CONTRADICTION = re.compile(
    r"\b(?:not down|not failed|was resolved|now healthy|false alarm|"
    r"not missing|not the cause|no longer failing|unverified claim)\b", re.I
)

def classify(note: object) -> dict:
    """Conservative deterministic reference; not a learned model."""
    abstain = {"category": "insufficient_evidence", "evidence": None}
    if type(note) is not str or not 30 <= len(note) <= 4000:
        return abstain
    marks = list(_MARKER.finditer(note))
    if not marks or len(marks) > 9:
        return abstain
    verified = []
    for i, match in enumerate(marks):
        text = note[match.end():marks[i + 1].start() if i + 1 < len(marks) else len(note)]
        if re.search(r"\bverified\b", (match.group("header") or "") + " " + text[:65], re.I):
            verified.append((match.group("id").upper(), text))
    if not verified:
        return abstain
    marker, evidence = verified[-1]
    if _CONTRADICTION.search(evidence):
        return {"category": "insufficient_evidence", "evidence": marker}
    matches = [code for code, pats in _PATTERNS.items()
               if all(re.search(p, evidence, re.I) for p in pats)]
    if len(matches) != 1:
        return {"category": "insufficient_evidence", "evidence": marker}
    return {"category": matches[0], "evidence": marker}
