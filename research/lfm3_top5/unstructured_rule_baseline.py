"""Post-hoc, non-generalizable deterministic baseline for SIX synthetic notes.

This keyword baseline was authored AFTER seeing the six public prompts.
It is intentionally NOT a blinded benchmark competitor or deployable
classifier. It shows that the guided scenarios may not require LFM3.
Unknown, absent, conflicting, or ambiguous evidence -> ABSTAIN.
"""
from __future__ import annotations
import re

def triage_rule(note):
    base={"decision":"ABSTAIN","category":None,"evidence":None,
          "source_custody_independently_attested":False,
          "production_authorized":False}
    if type(note) is not str or not 50<=len(note)<=2000:
        return base
    m=re.search(r"\bE3\b\s*(?:\([^)]{1,100}\))?\s*:\s*(.*)$",note,re.S)
    if m is None:return base
    e3=m.group(1).lower()
    patterns={
      "tls_sni_missing":("tlsv1_unrecognized_name","lacks a hostname/sni"),
      "node_auth_contract":("expects general basic authentication","node-specific task credentials are not accepted"),
      "gpu_attestation_missing":("no kernel-level signed execution witness","cannot be externally attested"),
      "beta_build_unlinked":("beta-testing group contains no linked build","eligible testers cannot install"),
      "database_service_down":("postgresql has no process listening on port 5432","service health is failed"),
      "insufficient_evidence":("no live trace","not enough evidence to assign a root cause"),
    }
    matched=[name for name,phrases in patterns.items()
             if all(phrase in e3 for phrase in phrases)]
    if len(matched)!=1:return base
    return {
      "decision":"MATCHED_POSTHOC_SYNTHETIC_RULE_RESEARCH_ONLY",
      "category":matched[0],"evidence":"E3",
      "source_custody_independently_attested":False,
      "production_authorized":False,
    }
