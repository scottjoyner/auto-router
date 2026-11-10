"""Synthetic RFC3161/three-role handoff integration; NEVER a real TSA witness."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from research.lfm3_top5.independent_custody_handoff import (
    KEY_SCHEMA, SCHEMA, COMMIT_SCHEMA, FROZEN_PREREG_SHA, ROLES, role_payload
)
from research.lfm3_top5.prospective_holdout_gate import MODEL_SHA, canonical, sha
from scripts.lfm3_rfc3161_anchor_verify import verify_timestamp, TOKEN_PREFIX
from tests.test_lfm3_independent_custody_handoff import fictional_cases

OPENSSL="/usr/bin/openssl"

def execute(args):
    run=subprocess.run(args,capture_output=True,text=True,timeout=15,check=False)
    if run.returncode:
        raise AssertionError("Synthetic fixture generation failed: "+run.stderr[-400:])
    return run

def certificate_fixture(root: Path, digest: str):
    ca=root/"synthetic-ca.pem";ca_key=root/"synthetic-ca.key"
    req=root/"synthetic-tsa.csr";tsa_key=root/"synthetic-tsa.key";tsa=root/"synthetic-tsa.pem"
    ext=root/"tsa-extensions.cnf";query=root/"query.tsq";reply=root/"reply.tsr"
    execute([OPENSSL,"req","-x509","-newkey","rsa:2048","-nodes","-days","1",
             "-keyout",str(ca_key),"-out",str(ca),
             "-subj","/CN=UNTRUSTED SYNTHETIC ROOT - NEVER PRODUCTION",
             "-addext","basicConstraints=critical,CA:TRUE",
             "-addext","keyUsage=critical,keyCertSign,cRLSign"])
    execute([OPENSSL,"req","-new","-newkey","rsa:2048","-nodes",
             "-keyout",str(tsa_key),"-out",str(req),
             "-subj","/CN=UNTRUSTED SYNTHETIC TSA - NEVER PRODUCTION"])
    ext.write_text("basicConstraints=critical,CA:FALSE\n"
                   "keyUsage=critical,digitalSignature\n"
                   "extendedKeyUsage=critical,timeStamping\n"
                   "subjectKeyIdentifier=hash\n"
                   "authorityKeyIdentifier=keyid,issuer\n")
    execute([OPENSSL,"x509","-req","-in",str(req),
             "-CA",str(ca),"-CAkey",str(ca_key),"-CAcreateserial",
             "-days","1","-out",str(tsa),"-extfile",str(ext)])
    execute([OPENSSL,"ts","-query","-sha256","-digest",digest,
             "-cert","-out",str(query)])
    execute([OPENSSL,"ts","-reply","-queryfile",str(query),
             "-signer",str(tsa),"-inkey",str(tsa_key),
             "-chain",str(ca),"-out",str(reply),
             "-tspolicy","1.2.3.4.1"])
    return query.read_bytes(),reply.read_bytes(),ca.read_bytes(),tsa.read_bytes()

def fake_signed_bundle(data, timestamp:bytes):
    """Three memory-only fake signers, not evidence of independent identity."""
    commitment={"schema":COMMIT_SCHEMA,
                "dataset_sha256":sha(data),
                "prereg_sha256":FROZEN_PREREG_SHA,
                "checkpoint_sha256":MODEL_SHA}
    keys={role:Ed25519PrivateKey.generate() for role in ROLES}
    roster={"schema":KEY_SCHEMA,"roles":{
        role:{"key_id":"synthetic."+role+".20261109",
              "ed25519_public_key_hex":keys[role].public_key().public_bytes(
                  encoding=serialization.Encoding.Raw,
                  format=serialization.PublicFormat.Raw).hex()}
        for role in ROLES}}
    bundle={"schema":SCHEMA,"commitment":commitment,
            "claimed_external_anchor_reference":
                TOKEN_PREFIX+hashlib.sha256(timestamp).hexdigest(),
            "signatures":{}}
    for role in ROLES:
        bundle["signatures"][role]={
            "key_id":roster["roles"][role]["key_id"],
            "signature_hex":keys[role].sign(role_payload(
                role,commitment,bundle["signatures"],
                bundle["claimed_external_anchor_reference"])).hex()}
    return bundle,roster

class TimestampIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch=tempfile.TemporaryDirectory(prefix="lfm3-synthetic-tsa-")
        cls.dir=Path(cls.scratch.name)
        cls.dataset=fictional_cases()
        commitment={"schema":COMMIT_SCHEMA,"dataset_sha256":sha(cls.dataset),
                    "prereg_sha256":FROZEN_PREREG_SHA,
                    "checkpoint_sha256":MODEL_SHA}
        imprint=hashlib.sha256(canonical(commitment).encode()).hexdigest()
        cls.query,cls.response,cls.ca,cls.tsa=certificate_fixture(cls.dir,imprint)
        cls.pin=hashlib.sha256(cls.ca).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def fixture(self):
        data=copy.deepcopy(self.dataset)
        bundle,roster=fake_signed_bundle(data,self.response)
        return data,bundle,roster,self.query,self.response,self.ca,self.tsa,self.pin

    def assert_hold(self,values,prefix=None):
        result=verify_timestamp(*values)
        self.assertEqual(result["decision"],"HOLD",result)
        for key in ("external_custody_verified","model_value_verified",
                    "production_authorized","trusted_timestamp_source_verified",
                    "pre_inference_chronology_verified",
                    "issuer_independently_authenticated"):
            self.assertFalse(result[key],key)
        if prefix:
            self.assertTrue(result["reason"].startswith(prefix),result)
        return result

    def test_true_cryptographic_verification_without_external_trust(self):
        result=verify_timestamp(*self.fixture())
        self.assertEqual(result["decision"],"PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY",result)
        self.assertTrue(result["offline_timestamp_crypto_verified"])
        self.assertTrue(result["timestamp_query_bound_to_dataset"])
        self.assertTrue(result["witness_signature_bound_to_token"])
        self.assertFalse(result["issuer_independently_authenticated"])
        self.assertFalse(result["nonce_origin_verified"])
        self.assertFalse(result["pre_inference_chronology_verified"])
        self.assertFalse(result["external_custody_verified"])
        self.assertFalse(result["production_authorized"])

    def test_dataset_modified_after_signing_holds(self):
        pack=list(self.fixture())
        pack[0]["cases"][0]["note"]+=" tampered"
        self.assert_hold(pack,"handoff_")

    def test_gold_marker_modified_after_signing_holds(self):
        pack=list(self.fixture())
        pack[0]["cases"][0]["expected"]["evidence"]="E2"
        self.assert_hold(pack,"handoff_")

    def test_missing_or_mismatched_ca_pin_holds(self):
        pack=list(self.fixture());pack[-1]="0"*64
        self.assert_hold(pack,"ca_certificate_pin_mismatch")
        pack=list(self.fixture());pack[-1]=None
        self.assert_hold(pack,"ca_certificate_pin_mismatch")

    def test_modified_token_signature_invalid_even_if_new_bundle_signed(self):
        pack=list(self.fixture())
        altered=bytearray(pack[4])
        altered[-10]^=1
        pack[4]=bytes(altered)
        pack[1],pack[2]=fake_signed_bundle(pack[0],pack[4])
        self.assert_hold(pack)

    def test_modified_token_with_old_witness_signature_holds(self):
        pack=list(self.fixture())
        pack[4]=pack[4][:-1]+bytes([pack[4][-1]^1])
        self.assert_hold(pack,"witness_signed_anchor_not_same_timestamp_token")

    def test_mismatched_query_imprint_holds(self):
        pack=list(self.fixture())
        other=self.dir/"other-query.tsq"
        execute([OPENSSL,"ts","-query","-sha256","-digest","0"*64,
                 "-cert","-out",str(other)])
        pack[3]=other.read_bytes()
        self.assert_hold(pack,"timestamp_query_imprint_or_nonce_mismatch")

    def test_mismatched_query_nonce_holds(self):
        pack=list(self.fixture())
        new=self.dir/"new-nonce.tsq"
        execute([OPENSSL,"ts","-query","-sha256",
                 "-digest",hashlib.sha256(canonical(pack[1]["commitment"]).encode()).hexdigest(),
                 "-cert","-out",str(new)])
        pack[3]=new.read_bytes()
        self.assert_hold(pack,"invalid_timestamp_response_nonce_or_signer_chain")

    def test_query_without_nonce_holds(self):
        pack=list(self.fixture())
        other=self.dir/"no-nonce.tsq"
        execute([OPENSSL,"ts","-query","-sha256",
                 "-digest",hashlib.sha256(canonical(pack[1]["commitment"]).encode()).hexdigest(),
                 "-no_nonce","-cert","-out",str(other)])
        pack[3]=other.read_bytes()
        self.assert_hold(pack,"timestamp_query_imprint_or_nonce_mismatch")

    def test_untrusted_root_substitution_holds(self):
        pack=list(self.fixture())
        # The test leaf is not the independent trusted root. Replacing the
        # pinned trust anchor with that leaf cannot validate the TSA chain.
        pack[5]=pack[6]
        pack[7]=hashlib.sha256(pack[5]).hexdigest()
        self.assert_hold(pack,"invalid_timestamp_response_nonce_or_signer_chain")

    def test_chain_modified_holds(self):
        pack=list(self.fixture())
        pack[6]=b"no cert here"
        self.assert_hold(pack,"missing_or_unsafe_certificate_bundle")

    def test_bad_types_and_noncanonical_der_holds(self):
        for index in (3,4):
            for bad in (None,True,[],{},b"\x30\x80",b"\x30\x00",b"\x30\x00"+b"junk"):
                pack=list(self.fixture());pack[index]=bad
                self.assert_hold(pack)
        for index in (5,6):
            pack=list(self.fixture());pack[index]=None
            self.assert_hold(pack)

    def test_self_signed_synthetic_ca_never_proves_external_issuer(self):
        out=verify_timestamp(*self.fixture())
        self.assertFalse(out["trusted_timestamp_source_verified"])
        self.assertFalse(out["issuer_independently_authenticated"])
        self.assertFalse(out["pre_inference_chronology_verified"])

    def _private_cli_fixture(self, root):
        values=self.fixture()
        sources={
            "dataset":(values[0],True),"bundle":(values[1],True),
            "key-roster":(values[2],True),"timestamp-query":(values[3],False),
            "timestamp-response":(values[4],False),"ca-pem":(values[5],False),
            "signer-chain-pem":(values[6],False),
        }
        cmd=[sys.executable,"-m","scripts.lfm3_rfc3161_anchor_verify"]
        for name,(value,as_json) in sources.items():
            file=root/name
            if as_json:file.write_text(json.dumps(value,sort_keys=True))
            else:file.write_bytes(value)
            file.chmod(0o600)
            cmd.extend(["--"+name,str(file)])
        cmd.extend(["--ca-pin-sha256",values[7]])
        return cmd,values

    def _private_cli_hold(self,cmd,values):
        out=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
        self.assertEqual(out.returncode,2,(out.stdout,out.stderr))
        body=json.loads(out.stdout)
        self.assertEqual(body['decision'],'HOLD')
        self.assertFalse(body['production_authorized'])
        self.assertFalse(body['external_custody_verified'])
        self.assertNotIn(values[0]['cases'][0]['note'],out.stdout)
        self.assertNotIn(values[0]['cases'][0]['id'],out.stdout)
        return body

    def test_cli_requires_owner_private_input_modes_and_parent_directory(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-ts-private-modes-') as t:
            root=Path(t);cmd,values=self._private_cli_fixture(root)
            good=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
            self.assertEqual(good.returncode,0,good.stderr)
            for name in ('dataset','bundle','key-roster','timestamp-query',
                         'timestamp-response','ca-pem','signer-chain-pem'):
                file=root/name
                for mode in (0o644,0o640,0o700):
                    with self.subTest(name=name,mode=oct(mode)):
                        file.chmod(mode)
                        self._private_cli_hold(cmd,values)
                        file.chmod(0o600)
            root.chmod(0o755)
            self._private_cli_hold(cmd,values)
            root.chmod(0o700)
            self.assertEqual(subprocess.run(cmd,capture_output=True,timeout=15).returncode,0)

    def test_cli_rejects_symlinked_parent_and_final_file_and_hardlinks(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-ts-private-links-') as t:
            root=Path(t);cmd,values=self._private_cli_fixture(root)
            alias=root/'alias';alias.symlink_to(root,target_is_directory=True)
            for name in ('dataset','timestamp-response','ca-pem'):
                with self.subTest(kind='parent',name=name):
                    changed=list(cmd);changed[changed.index('--'+name)+1]=str(alias/name)
                    self._private_cli_hold(changed,values)
            alias.unlink()
            response=root/'timestamp-response';orig=response.read_bytes()
            response.rename(root/'real-response')
            response.symlink_to(root/'real-response')
            self._private_cli_hold(cmd,values)
            response.unlink();(root/'real-response').rename(response)
            extra=root/'hardlink'
            os.link(response,extra)
            self._private_cli_hold(cmd,values)
            extra.unlink()
            response.unlink();os.mkfifo(response,mode=0o600)
            self._private_cli_hold(cmd,values)
            response.unlink();response.write_bytes(orig);response.chmod(0o600)
            self.assertEqual(subprocess.run(cmd,capture_output=True,timeout=15).returncode,0)

    def test_cli_rejects_oversize_inputs_duplicate_keys_and_noncanonical_paths(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-ts-private-size-') as t:
            root=Path(t);cmd,values=self._private_cli_fixture(root)
            for name,limit in (('dataset',2*1024*1024),('bundle',65536),
                               ('key-roster',65536),('timestamp-query',131072),
                               ('ca-pem',262144)):
                with self.subTest(name=name):
                    file=root/name;original=file.read_bytes()
                    file.write_bytes(original+b' '*(limit+1-len(original)))
                    self._private_cli_hold(cmd,values)
                    file.write_bytes(original)
            roster=root/'key-roster';original=roster.read_bytes()
            roster.write_bytes(b'{"schema":"abc","schema":"def"}')
            self._private_cli_hold(cmd,values)
            roster.write_bytes(original)
            changed=list(cmd)
            changed[changed.index('--dataset')+1]=str(root/'..'/root.name/'dataset')
            self._private_cli_hold(changed,values)
            self.assertEqual(subprocess.run(cmd,capture_output=True,timeout=15).returncode,0)

    def test_cli_pass_and_tampered_reply_hold(self):
        pack=self.fixture()
        with tempfile.TemporaryDirectory(prefix="lfm3-ts-cli-") as t:
            root=Path(t)
            inputs={
                "dataset":(pack[0],True),"bundle":(pack[1],True),
                "key-roster":(pack[2],True),"timestamp-query":(pack[3],False),
                "timestamp-response":(pack[4],False),"ca-pem":(pack[5],False),
                "signer-chain-pem":(pack[6],False),
            }
            cmd=[sys.executable,"-m","scripts.lfm3_rfc3161_anchor_verify"]
            for name,(value,as_json) in inputs.items():
                file=root/name
                if as_json:file.write_text(json.dumps(value,sort_keys=True))
                else:file.write_bytes(value)
                file.chmod(0o600)
                cmd+=["--"+name,str(file)]
            cmd+=["--ca-pin-sha256",pack[7]]
            good=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
            self.assertEqual(good.returncode,0,good.stderr)
            outcome=json.loads(good.stdout)
            self.assertEqual(outcome["decision"],"PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY")
            self.assertNotIn(pack[0]["cases"][0]["note"],good.stdout)
            (root/"timestamp-response").write_bytes(pack[4]+b"junk")
            bad=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
            self.assertEqual(bad.returncode,2)
            self.assertEqual(json.loads(bad.stdout)["decision"],"HOLD")
            (root/"timestamp-query").unlink()
            missing=subprocess.run(cmd,text=True,capture_output=True,timeout=15)
            self.assertEqual(missing.returncode,2)
            self.assertEqual(json.loads(missing.stdout)["decision"],"HOLD")

if __name__=="__main__":
    unittest.main()
