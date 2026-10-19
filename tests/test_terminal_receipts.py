"""Research contract: signed terminal custody, duplicate and fence denial."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import time
import os
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from auto_router.terminal_receipts import (
    DOMAIN, SignedTerminalCustody, ReceiptDenied, ReceiptStorageUnavailable,
    canonical, expected_binding
)

OWNER={"owner_issuer":"test-idp","owner_subject":"private-owner-ALPHA"}
CLAIM={"task_id":"task-1","claim_id":"claim-1","agent_id":"agent-1",
       "projection_generation":81,"jti":"secret-test-jti-ALPHA","attempt":1}
TIME=1792400000


def make(*,owner=None,claim=None,req="request-1",model="lenovo-lfm-cpu",
         runtime="lenovo-cpu",epoch=17,status="completed",now=TIME):
    expect=expected_binding(
        owner=owner or OWNER,claim=claim or CLAIM,request_id=req,
        model_id=model,runtime_instance_id=runtime,fence_epoch=epoch
    )
    payload={
        "schema":1,"audience":DOMAIN,"receipt_id":"receipt-"+req,
        **expect,"outcome":status,
        "output_sha256":"a"*64 if status=="completed" else None,
        "completion_tokens":3 if status=="completed" else 0,
        "finished_epoch_s":now
    }
    return expect,payload


@pytest.fixture
def authority(tmp_path):
    private=Ed25519PrivateKey.generate()
    pem=private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    filename=tmp_path/"terminal.sqlite"
    store=SignedTerminalCustody(str(filename),verifying_key_pem=pem,signer_id="test-result-signer")
    def seal(payload,*,signer=None):
        signed=canonical(payload)
        signature=(signer or private).sign(signed)
        return {"payload":payload,
                "signature":base64.urlsafe_b64encode(signature).rstrip(b"=").decode(),
                "signer_id":"test-result-signer"}
    return store,seal,pem,str(filename)


def test_valid_terminal_success_and_duplicate_receipt(authority):
    store,seal,_,_=authority
    expected,payload=make()
    envelope=seal(payload)
    assert store.accept(envelope=envelope,expected=expected,now_s=TIME)=="recorded"
    assert store.accept(envelope=envelope,expected=expected,now_s=TIME)=="duplicate"
    assert store.get("request-1")==envelope


def test_reopened_store_preserves_signed_terminal_result(authority):
    store,seal,pem,path=authority
    expected,payload=make()
    envelope=seal(payload)
    store.accept(envelope=envelope,expected=expected,now_s=TIME)
    reopened=SignedTerminalCustody(path,verifying_key_pem=pem,signer_id="test-result-signer")
    assert reopened.get("request-1")==envelope
    assert reopened.accept(envelope=envelope,expected=expected,now_s=TIME)=="duplicate"


def test_owner_switch_even_with_valid_signer_denied(authority):
    store,seal,_,_=authority
    trusted,_=make()
    _,wrong=make(owner={**OWNER,"owner_subject":"private-owner-BETA"})
    with pytest.raises(ReceiptDenied,match="owner_hash"):
        store.accept(envelope=seal(wrong),expected=trusted,now_s=TIME)
    assert store.get("request-1") is None


@pytest.mark.parametrize("changes",[
    {"claim_id":"claim-swapped"},
    {"agent_id":"agent-swapped"},
    {"task_id":"task-swapped"},
    {"projection_generation":82},
    {"jti":"different-JTI"},
    {"attempt":2},
])
def test_swapped_claim_denied(authority,changes):
    store,seal,_,_=authority
    expected,_=make()
    _,payload=make(claim={**CLAIM,**changes})
    with pytest.raises(ReceiptDenied,match="authority mismatch"):
        store.accept(envelope=seal(payload),expected=expected,now_s=TIME)


def test_stale_fence_epoch_denied(authority):
    store,seal,_,_=authority
    expected,_=make(epoch=18)
    _,stale=make(epoch=17)
    with pytest.raises(ReceiptDenied,match="fence_epoch"):
        store.accept(envelope=seal(stale),expected=expected,now_s=TIME)


def test_stale_projection_generation_denied(authority):
    store,seal,_,_=authority
    expected,_=make(claim={**CLAIM,"projection_generation":82})
    _,old=make()
    with pytest.raises(ReceiptDenied,match="authority mismatch"):
        store.accept(envelope=seal(old),expected=expected,now_s=TIME)


def test_tampered_status_breaks_signature(authority):
    store,seal,_,_=authority
    expected,payload=make()
    envelope=seal(payload)
    envelope["payload"]={**payload,"outcome":"failed","output_sha256":None,
                         "completion_tokens":0}
    with pytest.raises(ReceiptDenied,match="signature"):
        store.accept(envelope=envelope,expected=expected,now_s=TIME)


def test_attacker_with_different_key_cannot_sign(authority):
    store,seal,_,_=authority
    expected,payload=make()
    other=Ed25519PrivateKey.generate()
    with pytest.raises(ReceiptDenied,match="signature"):
        store.accept(envelope=seal(payload,signer=other),expected=expected,now_s=TIME)


def test_unknown_signer_id_rejected(authority):
    store,seal,_,_=authority
    expected,payload=make()
    envelope=seal(payload)
    envelope["signer_id"]="untrusted-issuer"
    with pytest.raises(ReceiptDenied,match="untrusted"):
        store.accept(envelope=envelope,expected=expected,now_s=TIME)


def test_missing_independent_authority_rejected(authority):
    store,seal,_,_=authority
    _,payload=make()
    with pytest.raises(ReceiptDenied,match="authority mismatch"):
        store.accept(envelope=seal(payload),expected={},now_s=TIME)


def test_conflicting_terminal_failure_cannot_overwrite_completed(authority):
    store,seal,_,_=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    _,failed=make(status="failed")
    with pytest.raises(ReceiptDenied,match="conflicting"):
        store.accept(envelope=seal(failed),expected=expected,now_s=TIME)
    assert store.get("request-1")["payload"]["outcome"]=="completed"


def test_uncertain_upstream_never_becomes_completed(authority):
    store,seal,_,_=authority
    expected,payload=make(status="possibly_accepted")
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    assert store.get("request-1")["payload"]["outcome"]=="possibly_accepted"
    _,complete=make(status="completed")
    with pytest.raises(ReceiptDenied,match="conflicting"):
        store.accept(envelope=seal(complete),expected=expected,now_s=TIME)


def test_terminal_cancelled_has_no_completion_proof(authority):
    store,seal,_,_=authority
    expected,payload=make(status="cancelled")
    assert store.accept(envelope=seal(payload),expected=expected,now_s=TIME)=="recorded"
    assert store.get("request-1")["payload"]["completion_tokens"]==0


def test_duplicate_receipt_id_cannot_serve_two_requests(authority):
    store,seal,_,_=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    second,other=make(req="request-2")
    other["receipt_id"]=payload["receipt_id"]
    with pytest.raises(ReceiptDenied,match="reused"):
        store.accept(envelope=seal(other),expected=second,now_s=TIME)


def test_same_request_different_owner_cannot_overwrite(authority):
    store,seal,_,_=authority
    exp,payload=make()
    store.accept(envelope=seal(payload),expected=exp,now_s=TIME)
    different,wrong=make(owner={**OWNER,"owner_subject":"someone-else"})
    with pytest.raises(ReceiptDenied,match="conflicting"):
        store.accept(envelope=seal(wrong),expected=different,now_s=TIME)


def test_parallel_duplicates_admit_exactly_one(authority):
    _,seal,pem,path=authority
    expected,payload=make()
    envelope=seal(payload)
    def run(_):
        return SignedTerminalCustody(path,verifying_key_pem=pem,
                         signer_id="test-result-signer").accept(
                         envelope=envelope,expected=expected,now_s=TIME)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(run,range(24)))
    assert results.count("recorded")==1
    assert results.count("duplicate")==23


def test_parallel_conflicting_signers_cannot_both_commit(authority):
    _,seal,pem,path=authority
    expected,complete=make()
    _,failed=make(status="failed")
    envelopes=(seal(complete),seal(failed))
    def run(i):
        try:
            result=SignedTerminalCustody(path,verifying_key_pem=pem,
                         signer_id="test-result-signer").accept(
                         envelope=envelopes[i%2],expected=expected,now_s=TIME)
            return result
        except ReceiptDenied:return "rejected"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(run,range(16)))
    assert "recorded" in results
    assert "rejected" in results
    final=SignedTerminalCustody(path,verifying_key_pem=pem,
                       signer_id="test-result-signer").get("request-1")
    assert final["payload"]["outcome"] in ("completed","failed")


def test_clock_stale_or_future_denied(authority):
    store,seal,_,_=authority
    expected,payload=make()
    for t in (TIME+301,TIME-301):
        with pytest.raises(ReceiptDenied,match="clock"):
            store.accept(envelope=seal(payload),expected=expected,now_s=t)


def test_noncompletion_claiming_tokens_denied(authority):
    _,seal,_,_=authority
    _,payload=make(status="failed")
    payload["completion_tokens"]=1
    with pytest.raises(ReceiptDenied,match="non-completion"):
        seal(payload)


def test_completion_without_output_digest_denied(authority):
    _,seal,_,_=authority
    _,payload=make()
    payload["output_sha256"]=None
    with pytest.raises(ReceiptDenied,match="output digest"):
        seal(payload)


@pytest.mark.parametrize("patch",[
    {"fence_epoch":True},{"attempt":False},{"projection_generation":0},
    {"outcome":"unknown"},{"completion_tokens":-1},
    {"audience":"other-product"},{"owner_hash":"a"*63}
])
def test_malformed_payload_denied(authority,patch):
    _,seal,_,_=authority
    _,payload=make()
    payload.update(patch)
    with pytest.raises(ReceiptDenied):
        seal(payload)


def test_db_requires_private_directory_and_file(authority,tmp_path):
    _,_,pem,_=authority
    public=tmp_path/"public"
    public.mkdir(mode=0o755)
    with pytest.raises(ReceiptStorageUnavailable):
        SignedTerminalCustody(str(public/"receipts.db"),verifying_key_pem=pem,
                              signer_id="test-result-signer")
    path=tmp_path/"bad.db"
    path.write_bytes(b"")
    path.chmod(0o644)
    with pytest.raises(ReceiptStorageUnavailable):
        SignedTerminalCustody(str(path),verifying_key_pem=pem,
                              signer_id="test-result-signer")


def test_symlink_db_rejected(authority,tmp_path):
    _,_,pem,_=authority
    innocent=tmp_path/"innocent"
    innocent.write_text("keep")
    target=tmp_path/"receipts-symlink.sqlite"
    target.symlink_to(innocent)
    with pytest.raises(ReceiptStorageUnavailable):
        SignedTerminalCustody(str(target),verifying_key_pem=pem,
                              signer_id="test-result-signer")
    assert innocent.read_text()=="keep"


def test_no_raw_owner_or_jti_in_recorded_evidence(authority):
    store,seal,_,path=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    raw=Path(path).read_bytes()
    assert b"private-owner-ALPHA" not in raw
    assert b"secret-test-jti-ALPHA" not in raw


def test_stored_payload_corruption_denied(authority):
    store,seal,_,path=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE signed_terminal_receipts SET payload=?",
                     (b'{"corrupted":true}',))
        conn.commit()
    with pytest.raises(ReceiptDenied,match="schema"):
        store.get("request-1")


def test_owner_identity_from_http_metadata_is_not_authority():
    with pytest.raises(ReceiptDenied,match="absent"):
        expected_binding(owner={},claim=CLAIM,request_id="request-1",
                 model_id="lenovo-lfm-cpu",runtime_instance_id="lenovo-cpu",
                 fence_epoch=17)


def test_missing_fence_epoch_denied():
    with pytest.raises(ReceiptDenied,match="fencing"):
        expected_binding(owner=OWNER,claim=CLAIM,request_id="request-1",
                 model_id="lenovo-lfm-cpu",runtime_instance_id="lenovo-cpu",
                 fence_epoch=None)


def test_separate_process_can_read_receipt_and_verify_signature(authority):
    store,seal,pem,path=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    pem_file=Path(path).with_suffix(".pub")
    pem_file.write_bytes(pem)
    code=(
        "import sys;"
        "from auto_router.terminal_receipts import SignedTerminalCustody;"
        "x=SignedTerminalCustody(sys.argv[1],verifying_key_pem=open(sys.argv[2],'rb').read(),"
        "signer_id='test-result-signer');"
        "print(x.get('request-1')['payload']['outcome'])"
    )
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"src")}
    result=subprocess.run([sys.executable,"-c",code,path,str(pem_file)],
        env=env,text=True,capture_output=True,timeout=8,check=True)
    assert result.stdout.strip()=="completed"



def test_indexed_owner_field_corruption_denied(authority):
    store,seal,_,path=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE signed_terminal_receipts SET owner_hash=?",("0"*64,))
    with pytest.raises(ReceiptDenied,match="indexed fields"):
        store.get("request-1")


def test_invalid_base64_signature_rejected(authority):
    store,seal,_,_=authority
    expected,payload=make()
    envelope=seal(payload)
    envelope["signature"]="!" + envelope["signature"][1:]
    with pytest.raises(ReceiptDenied,match="base64"):
        store.accept(envelope=envelope,expected=expected,now_s=TIME)


def test_receipt_cannot_be_rebound_to_another_request_key(authority):
    store,seal,_,path=authority
    expected,payload=make()
    store.accept(envelope=seal(payload),expected=expected,now_s=TIME)
    from auto_router.terminal_receipts import digest
    request_hash=digest("terminal-request-v1",["request-2"])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE signed_terminal_receipts SET request_hash=?",
                     (request_hash,))
    with pytest.raises(ReceiptDenied,match="indexed fields"):
        store.get("request-2")


def test_split_brain_counterexample_independent_hosts_both_accept(authority,tmp_path):
    """Explicit NO-GO witness: local SQLite is NOT a cross-host arbiter.

    Each independent store starts with the same valid public signer trust root,
    signed receipt, active-claim expectation and fencing epoch. Neither store
    can see the other's INSERT. This must NOT be promoted to global admission.
    """
    first,seal,pem,_=authority
    second=SignedTerminalCustody(
        str(tmp_path/"another-router-host-simulated.db"),
        verifying_key_pem=pem,signer_id="test-result-signer"
    )
    expected,payload=make()
    signed=seal(payload)
    assert first.accept(envelope=signed,expected=expected,now_s=TIME)=="recorded"
    assert second.accept(envelope=signed,expected=expected,now_s=TIME)=="recorded"
