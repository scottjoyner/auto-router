"""Verify one PUBLIC LFM3 research contract's externally returned RFC3161 token.

Pinned FreeTSA CA/leaf fingerprints came from the provider's HTTPS page.
This proves only cryptographic integrity against that provider-published root,
NOT independent TSA identity, pre-inference case custody, or release authority.
No network, signing, model inference, or production access.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile

from research.lfm3_top5.prospective_holdout_gate import canonical
from research.lfm3_top5.unstructured_triage_audit import _invalid_constant, _pairs_no_duplicate
from scripts.lfm3_rfc3161_anchor_verify import (
    MAX_DER, MAX_PEM, OPENSSL, _query_imprint_is_expected, _safe_run,
    _single_der_sequence
)

RECEIPT_SCHEMA = "lfm3.public-frozen-contract-rfc3161-receipt.v2"
CONTRACT_SCHEMA = "lfm3.public-frozen-research-contract-anchor.v1"
PIN_CONTRACT_SHA256 = "1f9a6bd4129a104e8e1846ed1a4c3727fc0f4834cb58353f877801dd833803a2"
PIN_QUERY_SHA256 = "02f86836879cff924fe01362126c9713f3c8021744e4a9bd4507e1ffc14b3d96"
PIN_RESPONSE_SHA256 = "bfef99efe2b7afc3d32f7a9f8d664a65baf1ac0ae8561c0a2b181bddcca696cd"
PIN_FREETSA_ROOT_PEM_SHA256 = "2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438"
PIN_FREETSA_TSA_PEM_SHA256 = "8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467"
PIN_FREETSA_ROOT_CRL_SHA256 = "13d85acc0dd6a7ace445822b09dcba0de4236e84d60494dcbe6d7a6c97031935"
PIN_CRL_LAST_UTC = "2026-09-18T10:30:55Z"
PIN_CRL_NEXT_UTC = "2027-09-18T10:30:55Z"
PIN_TSA_SERIAL_HEX = "C2E986160DA8E9CD"
PIN_ISSUED_TIME_UTC = "2026-11-09T14:34:22Z"
CONTRACT_KEYS = frozenset({
    "schema","scope","repo","source_head","source_tree","preregistration_sha256",
    "baseline_rule_sha256","prospective_scorer_sha256","checkpoint_expected_sha256",
    "case_dataset_sha256","independent_author_present","independent_gold_reviewer_present",
    "external_case_custody_verified","model_value_verified","production_authorized"
})
RECEIPT_KEYS = frozenset({
    "schema","scope","contract","contract_sha256","provider","provider_documentation",
    "provider_timestamp_endpoint","provider_published_ca_pem_sha256",
    "provider_published_tsa_pem_sha256","timestamp_response_sha256",
    "timestamp_query_sha256","tsa_claimed_utc_time",
    "timestamp_query_der_base64","timestamp_response_der_base64",
    "tsa_ca_pem_base64","tsa_signer_pem_base64",
    "provider_published_crl_url","provider_root_crl_pem_sha256",
    "provider_root_crl_pem_base64","provider_crl_last_update_utc",
    "provider_crl_next_update_utc","provider_tsa_signer_serial_hex",
    "provider_crl_independently_trusted",
    "independent_case_dataset_sealed","independent_tsa_trust_verified",
    "pre_inference_custody_verified","model_value_verified","production_authorized"
})

def hold(reason: str) -> dict:
    return {
        "decision":"HOLD",
        "reason":reason,
        "provider_token_signature_verified":False,
        "public_contract_digest_bound":False,
        "original_query_nonce_verified":False,
        "provider_ca_self_published_fingerprint_matches":False,
        "provider_crl_signature_verified":False,
        "signer_unrevoked_in_provider_crl_at_token_time":False,
        "provider_crl_independently_trusted":False,
        "provider_identity_independently_authenticated":False,
        "independent_case_dataset_sealed":False,
        "pre_inference_case_custody_verified":False,
        "earlier_development_runs_retroactively_validated":False,
        "model_value_verified":False,
        "production_authorized":False,
    }

def _decoded_base64(item: object, limit: int) -> bytes | None:
    if type(item) is not str or not 1 <= len(item) <= 4*((limit+2)//3):
        return None
    if re.fullmatch(r"[A-Za-z0-9+/]*={0,2}",item) is None:
        return None
    try:
        data=base64.b64decode(item,validate=True)
    except (binascii.Error,ValueError):
        return None
    if not data or len(data)>limit or base64.b64encode(data).decode()!=item:
        return None
    return data

def _exact_contract(obj: object) -> bool:
    return (
        type(obj) is dict and set(obj)==CONTRACT_KEYS
        and obj["schema"]==CONTRACT_SCHEMA
        and obj["scope"]=="PUBLIC_SOURCE_CONTRACT_ONLY_NOT_A_DATASET_SEAL"
        and obj["repo"]=="scottjoyner/auto-router"
        and obj["source_head"]=="4c532fc860d68c8590418bfc7ccb6e9db8448386"
        and obj["source_tree"]=="57d6a7a59519c53727aed49835be15f308c51d82"
        and obj["preregistration_sha256"]=="38aeb6eba605cfff44f6d46e15b0afe23b9fbe0b405561ae7848e1124d645696"
        and obj["baseline_rule_sha256"]=="29ecfbfdcd3c5606690718e0416f093e737609187f5e527ab4c0625089d3b18e"
        and obj["prospective_scorer_sha256"]=="d80c06ccf891aa680e65c160abfe4b4141a5dc0f6f6c5c957d061ac193e7c101"
        and obj["checkpoint_expected_sha256"]=="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
        and obj["case_dataset_sha256"] is None
        and all(obj[k] is False for k in (
            "independent_author_present","independent_gold_reviewer_present",
            "external_case_custody_verified","model_value_verified","production_authorized"
        ))
    )

def verify_public_anchor(record: object) -> dict:
    try:
        if type(record) is not dict or set(record)!=RECEIPT_KEYS or record["schema"]!=RECEIPT_SCHEMA:
            return hold("unexpected_public_receipt_shape")
        if (record["scope"]!="PUBLIC_FROZEN_CONTRACT_ONLY_NO_CASE_DATASET_SEAL"
            or record["provider"]!="FreeTSA"
            or record["provider_documentation"]!="https://freetsa.org/index_en.php"
            or record["provider_timestamp_endpoint"]!="https://freetsa.org/tsr"
            or record["provider_published_crl_url"]!="https://www.freetsa.org/crl/root_ca.crl"
            or any(record[k] is not False for k in (
                "independent_case_dataset_sealed","independent_tsa_trust_verified",
                "pre_inference_custody_verified","model_value_verified","production_authorized",
                "provider_crl_independently_trusted"
            ))):
            return hold("nonpublic_scope_or_claimed_release_authority")
        if not _exact_contract(record["contract"]):
            return hold("source_contract_identity_or_case_scope_mismatch")
        data=(canonical(record["contract"])+"\n").encode("ascii")
        imprint=hashlib.sha256(data).hexdigest()
        if record["contract_sha256"]!=imprint or imprint!=PIN_CONTRACT_SHA256:
            return hold("public_contract_sha256_mismatch")
        expected_pins=(
            ("timestamp_query_sha256",PIN_QUERY_SHA256),
            ("timestamp_response_sha256",PIN_RESPONSE_SHA256),
            ("provider_published_ca_pem_sha256",PIN_FREETSA_ROOT_PEM_SHA256),
            ("provider_published_tsa_pem_sha256",PIN_FREETSA_TSA_PEM_SHA256),
            ("provider_root_crl_pem_sha256",PIN_FREETSA_ROOT_CRL_SHA256)
        )
        if any(record[k]!=v for k,v in expected_pins):
            return hold("frozen_timestamp_or_provider_certificate_pin_mismatch")
        query=_decoded_base64(record["timestamp_query_der_base64"],MAX_DER)
        response=_decoded_base64(record["timestamp_response_der_base64"],MAX_DER)
        ca=_decoded_base64(record["tsa_ca_pem_base64"],MAX_PEM)
        signer=_decoded_base64(record["tsa_signer_pem_base64"],MAX_PEM)
        crl=_decoded_base64(record["provider_root_crl_pem_base64"],MAX_PEM)
        if any(x is None for x in (query,response,ca,signer,crl)):
            return hold("malformed_or_oversized_public_receipt_material")
        if not _single_der_sequence(query) or not _single_der_sequence(response):
            return hold("timestamp_query_or_reply_not_single_der_sequence")
        if (b"-----BEGIN CERTIFICATE-----" not in ca or
            b"-----BEGIN CERTIFICATE-----" not in signer or
            b"PRIVATE KEY" in ca or b"PRIVATE KEY" in signer or
            b"-----BEGIN X509 CRL-----" not in crl or b"PRIVATE KEY" in crl):
            return hold("unsafe_or_absent_public_certificates")
        if any(hashlib.sha256(v).hexdigest()!=h for v,h in (
            (query,PIN_QUERY_SHA256),(response,PIN_RESPONSE_SHA256),
            (ca,PIN_FREETSA_ROOT_PEM_SHA256),(signer,PIN_FREETSA_TSA_PEM_SHA256),
            (crl,PIN_FREETSA_ROOT_CRL_SHA256)
        )):
            return hold("raw_public_receipt_material_digest_mismatch")
        if (record["tsa_claimed_utc_time"]!=PIN_ISSUED_TIME_UTC or
            record["provider_crl_last_update_utc"]!=PIN_CRL_LAST_UTC or
            record["provider_crl_next_update_utc"]!=PIN_CRL_NEXT_UTC or
            record["provider_tsa_signer_serial_hex"]!=PIN_TSA_SERIAL_HEX):
            return hold("provider_signed_time_claim_mismatch")
        with tempfile.TemporaryDirectory(prefix="lfm3-public-contract-tsa-") as directory:
            root=Path(directory)
            q=root/"query.tsq";r=root/"response.tsr";c=root/"ca.pem"
            crt=root/"tsa.pem";crlf=root/"provider.crl"
            q.write_bytes(query);r.write_bytes(response);c.write_bytes(ca)
            crt.write_bytes(signer);crlf.write_bytes(crl)
            parsed=_safe_run([OPENSSL,"ts","-query","-in",str(q),"-text"])
            if parsed.returncode!=0 or not _query_imprint_is_expected(parsed.stdout,imprint):
                return hold("timestamp_query_not_bound_to_public_contract_or_nonce")
            verified=_safe_run([OPENSSL,"ts","-verify","-queryfile",str(q),
                                "-in",str(r),"-CAfile",str(c)])
            if verified.returncode!=0 or "Verification: OK" not in verified.stdout:
                return hold("timestamp_signature_nonce_or_ca_chain_invalid")
            metadata=_safe_run([OPENSSL,"ts","-reply","-in",str(r),"-text"])
            if metadata.returncode!=0:
                return hold("unreadable_signed_timestamp_metadata")
            candidates=re.findall(r"^Time stamp:\s*(.+?)\s*$",metadata.stdout,re.MULTILINE)
            if len(candidates)!=1:
                return hold("absent_or_ambiguous_provider_timestamp")
            stamp=datetime.strptime(candidates[0],"%b %d %H:%M:%S %Y GMT")
            observed=stamp.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if observed!=PIN_ISSUED_TIME_UTC:
                return hold("provider_signed_time_claim_mismatch")
            crl_metadata=_safe_run([OPENSSL,"crl","-in",str(crlf),
                                    "-noout","-lastupdate","-nextupdate"])
            if crl_metadata.returncode!=0:
                return hold("unparseable_provider_crl")
            dates=dict(line.split("=",1) for line in crl_metadata.stdout.splitlines() if "=" in line)
            if set(dates)!={"lastUpdate","nextUpdate"}:
                return hold("missing_provider_crl_validity_dates")
            last=datetime.strptime(dates["lastUpdate"],"%b %d %H:%M:%S %Y GMT")
            following=datetime.strptime(dates["nextUpdate"],"%b %d %H:%M:%S %Y GMT")
            if (last.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") != PIN_CRL_LAST_UTC or
                following.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") != PIN_CRL_NEXT_UTC or
                not last <= stamp <= following):
                return hold("provider_crl_does_not_cover_issued_timestamp")
            verified_crl=_safe_run([OPENSSL,"crl","-in",str(crlf),
                                    "-CAfile",str(c),"-verify","-noout"])
            if verified_crl.returncode!=0 or "verify OK" not in (verified_crl.stdout+verified_crl.stderr):
                return hold("provider_crl_signature_invalid")
            serial=_safe_run([OPENSSL,"x509","-in",str(crt),"-noout","-serial"])
            if serial.returncode!=0 or serial.stdout.strip()!=("serial="+PIN_TSA_SERIAL_HEX):
                return hold("provider_timestamp_signer_serial_mismatch")
            epoch=int(stamp.replace(tzinfo=timezone.utc).timestamp())
            revocation=_safe_run([OPENSSL,"verify","-crl_check","-attime",str(epoch),
                                  "-CAfile",str(c),"-CRLfile",str(crlf),str(crt)])
            if revocation.returncode!=0 or ": OK" not in revocation.stdout:
                return hold("provider_signer_revoked_or_crl_chain_invalid")
        return {
            "decision":"PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY",
            "public_contract_sha256":imprint,
            "timestamp_token_sha256":PIN_RESPONSE_SHA256,
            "provider_claimed_issued_utc":PIN_ISSUED_TIME_UTC,
            "provider_token_signature_verified":True,
            "public_contract_digest_bound":True,
            "original_query_nonce_verified":True,
            "provider_ca_self_published_fingerprint_matches":True,
            "provider_crl_signature_verified":True,
            "signer_unrevoked_in_provider_crl_at_token_time":True,
            "provider_crl_independently_trusted":False,
            "provider_identity_independently_authenticated":False,
            "independent_case_dataset_sealed":False,
            "pre_inference_case_custody_verified":False,
            "earlier_development_runs_retroactively_validated":False,
            "model_value_verified":False,
            "production_authorized":False,
            "reason":"Provider-issued public contract timestamp and issuer-signed CRL at token time verify against provider-published CA; independent trust and heldout case sealing remain unproven.",
        }
    except (TypeError,ValueError,KeyError,OverflowError,OSError,
            RecursionError,IndexError):
        return hold("untrusted_or_unreadable_public_anchor_receipt")

def _read_json(path: str) -> object:
    with open(path,encoding="utf-8") as stream:
        return json.load(stream,object_pairs_hook=_pairs_no_duplicate,
                         parse_constant=_invalid_constant)

def main() -> int:
    p=argparse.ArgumentParser(description="Verify pinned FreeTSA public prereg-contract receipt offline (NOT independent case custody)")
    p.add_argument("--receipt",default="research/lfm3_top5/public_contract_anchor/receipt.json")
    args=p.parse_args()
    try:
        with open(args.receipt,"rb") as stream:
            raw=stream.read(65537)
        if len(raw)>65536:
            result=hold("oversize_public_anchor_receipt")
        else:
            obj=json.loads(raw.decode("utf-8"),
                object_pairs_hook=_pairs_no_duplicate,parse_constant=_invalid_constant)
            result=verify_public_anchor(obj)
    except (OSError,ValueError,TypeError,RecursionError,UnicodeError):
        result=hold("unreadable_or_malformed_receipt_json")
    print(canonical(result))
    return 0 if result["decision"]=="PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
