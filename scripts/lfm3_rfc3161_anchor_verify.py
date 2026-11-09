"""Offline RFC 3161 timestamp token check for prospective LFM3 research only.

All CA roots, tokens and key rosters are caller-supplied. Cryptographic
verification against a caller-supplied pinned CA NEVER establishes an
independently trusted TSA issuer or pre-inference external custody.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile

from research.lfm3_top5.independent_custody_handoff import verify_handoff
from research.lfm3_top5.prospective_holdout_gate import canonical
from research.lfm3_top5.unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate

TOKEN_PREFIX = "sha256:"
MAX_DER = 131072
MAX_PEM = 262144
HEX64 = re.compile(r"[0-9a-f]{64}")
OPENSSL = "/usr/bin/openssl"

def hold(reason: str) -> dict:
    return {
        "decision": "HOLD",
        "reason": reason,
        "timestamp_query_bound_to_dataset": False,
        "offline_timestamp_crypto_verified": False,
        "witness_signature_bound_to_token": False,
        "issuer_independently_authenticated": False,
        "nonce_origin_verified": False,
        "pre_inference_chronology_verified": False,
        "trusted_timestamp_source_verified": False,
        "external_custody_verified": False,
        "model_value_verified": False,
        "production_authorized": False,
    }

def _single_der_sequence(data: object) -> bool:
    """Reject indefinite, noncanonical length and any trailing DER bytes."""
    if type(data) is not bytes or not 8 <= len(data) <= MAX_DER or data[0] != 0x30:
        return False
    initial = data[1]
    if initial < 128:
        length,offset=initial,2
    else:
        count=initial & 0x7f
        if count==0 or count>4 or len(data)<2+count:
            return False
        length=int.from_bytes(data[2:2+count],"big")
        if length<128 or data[2]==0:
            return False
        offset=2+count
    return length==len(data)-offset

def _safe_run(args: list[str], timeout: float = 10.0):
    return subprocess.run(args,capture_output=True,text=True,
                          timeout=timeout,check=False,stdin=subprocess.DEVNULL)

def _query_imprint_is_expected(dump: str, expected_hex: str) -> bool:
    """Strictly parse SHA-256 query's fixed 2x16-byte OpenSSL text dump."""
    lines=dump.splitlines()
    if not all(x in lines for x in ("Version: 1","Hash Algorithm: sha256",
                                    "Message data:","Certificate required: yes")):
        return False
    if sum(line.startswith("Nonce: 0x") for line in lines)!=1:
        return False
    start=lines.index("Message data:")
    try:
        end=next(i for i in range(start+1,len(lines)) if lines[i].startswith("Policy OID:"))
    except StopIteration:
        return False
    if end-start!=3:
        return False
    chunks=[]
    for index,line in enumerate(lines[start+1:end]):
        match=re.fullmatch(r"\s*(0000|0010)\s+-\s+(.+)",line)
        if match is None or match.group(1)!=("0000" if index==0 else "0010"):
            return False
        octets=match.group(2).split("   ",1)[0].replace("-"," ").split()
        if len(octets)!=16 or any(re.fullmatch(r"[0-9a-f]{2}",v) is None for v in octets):
            return False
        chunks.extend(octets)
    return "".join(chunks)==expected_hex

def verify_timestamp(dataset: object, bundle: object, key_roster: object,
                     query_der: object, response_der: object,
                     ca_pem: object, signer_chain_pem: object,
                     independent_ca_pin_sha256: object) -> dict:
    """Bind signer/witness agreement to token and check nonce + trust chain.

    'independent_ca_pin_sha256' must be fetched via an actually independent
    approved channel by an external reviewer. This function cannot verify
    that origin; even a valid self-signed test CA stays HOLD for release.
    """
    try:
        handoff=verify_handoff(dataset,bundle,key_roster)
        if handoff["decision"]!="PASS_OFFLINE_SIGNATURE_STRUCTURE_ONLY":
            return hold("handoff_"+handoff["reason"])
        if not _single_der_sequence(query_der) or not _single_der_sequence(response_der):
            return hold("malformed_or_noncanonical_timestamp_der")
        if (type(ca_pem) is not bytes or type(signer_chain_pem) is not bytes
            or not 512 <= len(ca_pem) <= MAX_PEM
            or not 512 <= len(signer_chain_pem) <= MAX_PEM
            or b"-----BEGIN CERTIFICATE-----" not in ca_pem
            or b"-----BEGIN CERTIFICATE-----" not in signer_chain_pem
            or b"PRIVATE KEY" in ca_pem or b"PRIVATE KEY" in signer_chain_pem):
            return hold("missing_or_unsafe_certificate_bundle")
        if (type(independent_ca_pin_sha256) is not str or
            HEX64.fullmatch(independent_ca_pin_sha256) is None or
            hashlib.sha256(ca_pem).hexdigest()!=independent_ca_pin_sha256):
            return hold("ca_certificate_pin_mismatch")
        expected_token_reference=TOKEN_PREFIX+hashlib.sha256(response_der).hexdigest()
        if (type(bundle) is not dict or
            bundle.get("claimed_external_anchor_reference")!=expected_token_reference):
            return hold("witness_signed_anchor_not_same_timestamp_token")
        commitment=bundle["commitment"]
        imprint=hashlib.sha256(canonical(commitment).encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory(prefix="lfm3-ts-verification-") as directory:
            root=Path(directory)
            query=root/"request.tsq"
            response=root/"reply.tsr"
            ca=root/"ca.pem"
            chain=root/"tsa-chain.pem"
            query.write_bytes(query_der)
            response.write_bytes(response_der)
            ca.write_bytes(ca_pem)
            chain.write_bytes(signer_chain_pem)
            inspection=_safe_run([OPENSSL,"ts","-query","-in",str(query),"-text"])
            if inspection.returncode!=0 or not _query_imprint_is_expected(
                inspection.stdout,imprint):
                return hold("timestamp_query_imprint_or_nonce_mismatch")
            verification=_safe_run([
                OPENSSL,"ts","-verify","-queryfile",str(query),
                "-in",str(response),"-CAfile",str(ca),
                "-untrusted",str(chain),"-verify_depth","3"
            ])
            if verification.returncode!=0 or "Verification: OK" not in verification.stdout:
                return hold("invalid_timestamp_response_nonce_or_signer_chain")
        return {
            "decision": "PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY",
            "commitment_imprint_sha256": imprint,
            "timestamp_token_sha256": hashlib.sha256(response_der).hexdigest(),
            "supplied_ca_pem_sha256": independent_ca_pin_sha256,
            "timestamp_query_bound_to_dataset": True,
            "offline_timestamp_crypto_verified": True,
            "witness_signature_bound_to_token": True,
            "issuer_independently_authenticated": False,
            "nonce_origin_verified": False,
            "pre_inference_chronology_verified": False,
            "trusted_timestamp_source_verified": False,
            "external_custody_verified": False,
            "model_value_verified": False,
            "production_authorized": False,
            "reason": "Timestamp query nonce, SHA256 imprint and RFC3161 signer chain verify against supplied CA; independent issuer and pre-inference time remain unproven.",
        }
    except (ValueError,TypeError,KeyError,OSError,OverflowError,
            RecursionError,subprocess.TimeoutExpired):
        return hold("untrusted_or_unavailable_timestamp_input")

def _read_json(path: str):
    with open(path,"r",encoding="utf-8") as stream:
        return json.load(stream,object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)

def _read_limited(path: str, limit: int) -> bytes:
    with open(path,"rb") as stream:
        data=stream.read(limit+1)
    if len(data)>limit:
        raise ValueError("timestamp file exceeds limit")
    return data

def main() -> int:
    parser=argparse.ArgumentParser(description="Verify LFM3 RFC3161 structure offline; never custody or production approval")
    for field in ("dataset","bundle","key-roster","timestamp-query","timestamp-response",
                  "ca-pem","signer-chain-pem","ca-pin-sha256"):
        parser.add_argument("--"+field,required=True)
    a=parser.parse_args()
    try:
        out=verify_timestamp(
            _read_json(a.dataset),_read_json(a.bundle),_read_json(a.key_roster),
            _read_limited(a.timestamp_query,MAX_DER),
            _read_limited(a.timestamp_response,MAX_DER),
            _read_limited(a.ca_pem,MAX_PEM),
            _read_limited(a.signer_chain_pem,MAX_PEM),
            a.ca_pin_sha256
        )
    except (TypeError,ValueError,OSError,RecursionError):
        out=hold("unreadable_or_oversize_local_timestamp_file")
    print(canonical(out))
    return 0 if out["decision"]=="PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
