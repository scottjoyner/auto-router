"""Externally signable bytes, using synthetic *in-memory-only* Ed25519 keys."""
from __future__ import annotations

import copy
import os
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from research.lfm3_top5.independent_custody_handoff import (
    KEY_SCHEMA, ROLES, role_payload,
)
from research.lfm3_top5.prewitness_commitment import inspect_prewitness
from research.lfm3_top5.signer_payload_handoff import (
    prepare_unsigned_payloads, assemble_external_role_signatures,
    verify_unsigned_role_payload,
)
from scripts.lfm3_prepare_prewitness_tsq import prepare_prewitness_query
from tests.test_lfm3_independent_custody_handoff import fictional_cases


def fixtures():
    data=fictional_cases()
    keys={role:Ed25519PrivateKey.generate() for role in ROLES}
    roster={'schema':KEY_SCHEMA,'roles':{
        role:{'key_id':f'fictional.unsigned-payload.{role}.20261109',
              'ed25519_public_key_hex':keys[role].public_key().public_bytes(
                  encoding=serialization.Encoding.Raw,
                  format=serialization.PublicFormat.Raw).hex()}
        for role in ROLES}}
    return data,roster,keys


def private_file(path,data):
    if type(data) is bytes:path.write_bytes(data)
    else:path.write_text(json.dumps(data,sort_keys=True))
    path.chmod(0o600)


def cli_fixture(root):
    d,r,keys=fixtures()
    private_file(root/'dataset.json',d)
    private_file(root/'roster.json',r)
    common=['--dataset',str(root/'dataset.json'),'--key-roster',str(root/'roster.json')]
    prepare=[sys.executable,'-m','scripts.lfm3_signer_payload_handoff',
             'prepare',*common,'--author-payload',str(root/'author.bin'),
             '--reviewer-payload',str(root/'reviewer.bin')]
    assemble=[sys.executable,'-m','scripts.lfm3_signer_payload_handoff',
              'assemble',*common,'--author-signature',str(root/'author.sig'),
              '--reviewer-signature',str(root/'reviewer.sig'),
              '--prewitness-output',str(root/'prewitness.json')]
    return d,r,keys,prepare,assemble


def run_cli(cmd):
    return subprocess.run(cmd,capture_output=True,text=True,timeout=15)


class ExternalSignerBytes(unittest.TestCase):
    def assert_hold(self,data,roster,sigs=None):
        if sigs is None:
            out,payload=prepare_unsigned_payloads(data,roster)
        else:
            out,payload=assemble_external_role_signatures(data,roster,sigs)
        self.assertEqual(out['decision'],'HOLD',out)
        self.assertIsNone(payload)
        for k in ('independent_author_verified','key_ownership_independently_authenticated',
                  'external_case_custody_verified','permitted_to_infer','production_authorized'):
            self.assertFalse(out[k])

    def test_exact_author_and_reviewer_bytes_contain_only_role_and_frozen_hashes(self):
        d,r,_=fixtures()
        out,payloads=prepare_unsigned_payloads(d,r)
        self.assertEqual(out['decision'],'PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY')
        self.assertEqual(set(payloads),{'author','reviewer'})
        self.assertNotEqual(payloads['author'],payloads['reviewer'])
        for role in ('author','reviewer'):
            p=payloads[role]
            self.assertTrue(p.startswith(('lfm3:prospective-custody:'+role+':v1\n').encode()))
            self.assertLess(len(p),4096)
            self.assertNotIn(d['cases'][0]['note'].encode(),p)
            self.assertNotIn(d['cases'][0]['id'].encode(),p)
            self.assertNotIn(b'"expected"',p)
            self.assertNotIn(b'"cases"',p)
            self.assertNotIn('payload_sha256' in out and d['cases'][0]['note'],json.dumps(out))
        self.assertFalse(out['independent_author_verified'])
        self.assertFalse(out['permitted_to_infer'])

    def test_signer_independently_checks_exact_author_or_reviewer_payload(self):
        d,r,_=fixtures()
        status,payloads=prepare_unsigned_payloads(d,r)
        self.assertEqual(status['decision'],'PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY')
        for role in ('author','reviewer'):
            result=verify_unsigned_role_payload(d,r,role,payloads[role])
            self.assertEqual(result['decision'],'PASS_STATIC_SIGNER_PAYLOAD_MATCH_ONLY')
            self.assertEqual(result['role'],role)
            self.assertTrue(result['payload_matches_supplied_dataset'])
            for field in ('independent_dataset_review_verified','independent_author_verified',
                          'key_ownership_independently_authenticated',
                          'permitted_to_infer','production_authorized'):
                self.assertFalse(result[field])
            self.assertNotIn(d['cases'][0]['note'],json.dumps(result))
        self.assertEqual(verify_unsigned_role_payload(d,r,'author',payloads['reviewer'])['decision'],'HOLD')
        self.assertEqual(verify_unsigned_role_payload(d,r,'reviewer',payloads['author'])['decision'],'HOLD')
        for role in (None, True, 'witness','model_developer',''):
            self.assertEqual(verify_unsigned_role_payload(d,r,role,payloads['author'])['decision'],'HOLD')

    def test_signer_payload_byte_tamper_and_changed_gold_fail_before_signing(self):
        d,r,_=fixtures()
        _,payloads=prepare_unsigned_payloads(d,r)
        for value in (payloads['author']+b'\n',payloads['author'][:-1],
                      payloads['author'].replace(b'author',b'review',1),b'',True,None,[],b'x'*4097):
            with self.subTest(data=repr(value)[:55]):
                status=verify_unsigned_role_payload(d,r,'author',value)
                self.assertEqual(status['decision'],'HOLD')
                self.assertFalse(status['permitted_to_infer'])
        altered=copy.deepcopy(d)
        altered['cases'][0]['note']+=' additional synthetic source text'
        self.assertEqual(verify_unsigned_role_payload(altered,r,'author',payloads['author'])['decision'],'HOLD')
        changed=copy.deepcopy(d)
        # Use a valid *different* gold label: signatures cannot be transferred.
        changed['cases'][0]['expected']['category']='tls_sni_missing' if changed['cases'][0]['expected']['category']!='tls_sni_missing' else 'node_auth_contract'
        self.assertEqual(verify_unsigned_role_payload(changed,r,'author',payloads['author'])['decision'],'HOLD')

    def test_actual_signer_verify_cli_before_detached_signature_assembly(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-review-own-payload-') as folder:
            root=Path(folder);d,r,keys,prepare,assemble=cli_fixture(root)
            self.assertEqual(run_cli(prepare).returncode,0)
            for role in ('author','reviewer'):
                verify=[sys.executable,'-m','scripts.lfm3_signer_payload_handoff',
                        'verify','--dataset',str(root/'dataset.json'),
                        '--key-roster',str(root/'roster.json'),
                        '--role',role,'--payload',str(root/(role+'.bin'))]
                rcv=run_cli(verify)
                self.assertEqual(rcv.returncode,0,rcv.stderr)
                out=json.loads(rcv.stdout)
                self.assertEqual(out['decision'],'PASS_STATIC_SIGNER_PAYLOAD_MATCH_ONLY')
                self.assertFalse(out['independent_dataset_review_verified'])
                self.assertFalse(out['permitted_to_infer'])
                self.assertNotIn(d['cases'][0]['note'],rcv.stdout)
                original=(root/(role+'.bin')).read_bytes()
                (root/(role+'.bin')).write_bytes(original+b'\n')
                self.assertEqual(run_cli(verify).returncode,2)
                (root/(role+'.bin')).write_bytes(original)
                (root/(role+'.bin')).chmod(0o644)
                self.assertEqual(run_cli(verify).returncode,2)
                (root/(role+'.bin')).chmod(0o600)
                self.assertEqual(run_cli(verify).returncode,0)
            original=root/'author.bin'
            other=root/'author-copy.bin';original.rename(other)
            original.symlink_to(other)
            args=[sys.executable,'-m','scripts.lfm3_signer_payload_handoff',
                  'verify','--dataset',str(root/'dataset.json'),
                  '--key-roster',str(root/'roster.json'),
                  '--role','author','--payload',str(original)]
            self.assertEqual(run_cli(args).returncode,2)

    def test_documented_openssl_pkeyutl_sign_rawin_interoperates_with_assembler(self):
        # This is a SELF-GENERATED temporary test signing key in a private
        # directory. It is NOT independent human signer/custody evidence.
        with tempfile.TemporaryDirectory(prefix='lfm3-openssl-interoperability-') as folder:
            root=Path(folder);d,r,keys,prepare,assemble=cli_fixture(root)
            self.assertEqual(run_cli(prepare).returncode,0)
            for role in ('author','reviewer'):
                private_pem=keys[role].private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                key_path=root/(role+'.synthetic-test-key.pem')
                private_file(key_path,private_pem)
                sign=subprocess.run(['openssl','pkeyutl','-sign','-rawin',
                    '-inkey',str(key_path),'-in',str(root/(role+'.bin')),
                    '-out',str(root/(role+'.sig'))],
                    capture_output=True,text=True,timeout=10)
                self.assertEqual(sign.returncode,0,sign.stderr)
                self.assertEqual((root/(role+'.sig')).stat().st_size,64)
                # Real signers must never hand their PEM keys to the custodian.
                key_path.unlink()
                (root/(role+'.sig')).chmod(0o600)
            good=run_cli(assemble)
            self.assertEqual(good.returncode,0,(good.stdout,good.stderr))
            self.assertEqual(json.loads(good.stdout)['decision'],
                             'PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY')
            self.assertFalse(json.loads(good.stdout)['key_ownership_independently_authenticated'])
            self.assertFalse(json.loads(good.stdout)['production_authorized'])

    def test_external_signature_verification_yields_existing_prewitness_schema(self):
        d,r,keys=fixtures()
        status,payloads=prepare_unsigned_payloads(d,r)
        detached={role:keys[role].sign(payloads[role]) for role in ('author','reviewer')}
        status,package=assemble_external_role_signatures(d,r,detached)
        self.assertEqual(status['decision'],'PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY',status)
        self.assertTrue(status['offline_signatures_verified'])
        self.assertFalse(status['key_ownership_independently_authenticated'])
        self.assertFalse(status['permitted_to_infer'])
        self.assertEqual(set(package),{'schema','commitment','signatures'})
        self.assertEqual(set(package['signatures']),{'author','reviewer'})
        self.assertEqual(inspect_prewitness(d,package,r)['decision'],
                         'PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY')
        tsq,raw=prepare_prewitness_query(d,package,r)
        self.assertEqual(tsq['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY')
        self.assertIsInstance(raw,bytes)
        self.assertFalse(tsq['permitted_to_infer'])

    def test_swapped_author_reviewer_signatures_fail(self):
        d,r,keys=fixtures()
        _,payloads=prepare_unsigned_payloads(d,r)
        detached={'author':keys['reviewer'].sign(payloads['reviewer']),
                  'reviewer':keys['author'].sign(payloads['author'])}
        self.assert_hold(d,r,detached)

    def test_wrong_signer_and_mismatched_source_fail(self):
        d,r,keys=fixtures()
        _,payloads=prepare_unsigned_payloads(d,r)
        detached={role:keys[role].sign(payloads[role]) for role in ('author','reviewer')}
        other=Ed25519PrivateKey.generate().sign(payloads['author'])
        self.assert_hold(d,r,{**detached,'author':other})
        d['cases'][0]['note']+=' altered after signer signed'
        self.assert_hold(d,r,detached)

    def test_reused_three_role_public_keys_missing_data_and_extra_flags_fail(self):
        d,r,keys=fixtures()
        r['roles']['witness']['ed25519_public_key_hex']=r['roles']['author']['ed25519_public_key_hex']
        self.assert_hold(d,r)
        d,r,_=fixtures()
        self.assert_hold(None,r)
        self.assert_hold(d,{})
        r['production_authorized']=True
        self.assert_hold(d,r)

    def test_detached_signature_file_lengths_and_wrong_types_hold(self):
        d,r,_=fixtures()
        for bad in (None,{},[],True,{'author':b'0'*64},
                    {'author':b'0'*64,'reviewer':b'1'*63},
                    {'author':b'0'*64,'reviewer':'x'*64},
                    {'author':b'0'*64,'reviewer':b'1'*64,'witness':b'x'*64}):
            with self.subTest(kind=repr(bad)[:70]):
                self.assert_hold(d,r,bad if bad is not None else {'author':b'1'})

    def test_live_cli_prepare_then_synthetic_external_sign_then_assemble(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-signer-handoff-cli-') as folder:
            root=Path(folder)
            d,r,keys,prepare,assemble=cli_fixture(root)
            first=run_cli(prepare)
            self.assertEqual(first.returncode,0,(first.stdout,first.stderr))
            status=json.loads(first.stdout)
            self.assertEqual(status['decision'],'PASS_OFFLINE_UNSIGNED_ROLE_PAYLOADS_ONLY')
            self.assertFalse(status['key_ownership_independently_authenticated'])
            self.assertFalse(status['production_authorized'])
            for role in ('author','reviewer'):
                file=root/(role+'.bin')
                self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
                self.assertNotIn(d['cases'][0]['note'],file.read_text())
                private_file(root/(role+'.sig'),keys[role].sign(file.read_bytes()))
            second=run_cli(assemble)
            self.assertEqual(second.returncode,0,(second.stdout,second.stderr))
            receipt=json.loads(second.stdout)
            self.assertEqual(receipt['decision'],'PASS_OFFLINE_DETACHED_ROLE_SIGNATURES_ONLY')
            self.assertFalse(receipt['permitted_to_infer'])
            bundle=root/'prewitness.json'
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode),0o600)
            self.assertEqual(inspect_prewitness(d,json.loads(bundle.read_text()),r)['decision'],
                             'PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY')
            for term in (d['cases'][0]['note'],d['cases'][0]['id'],d['cases'][0]['expected']['category']):
                self.assertNotIn(term,first.stdout+second.stdout)
            self.assertEqual(run_cli(prepare).returncode,2)
            self.assertEqual(run_cli(assemble).returncode,2)

    def test_cli_bad_public_roster_and_wrong_signer_yield_no_output(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-signer-bad-') as folder:
            root=Path(folder)
            d,r,keys,prepare,assemble=cli_fixture(root)
            other=Ed25519PrivateKey.generate()
            private_file(root/'author.sig',other.sign(b'not-signed-real-content'))
            private_file(root/'reviewer.sig',b'Z'*64)
            result=run_cli(assemble)
            self.assertEqual(result.returncode,2)
            self.assertFalse((root/'prewitness.json').exists())
            (root/'roster.json').chmod(0o644)
            self.assertEqual(run_cli(prepare).returncode,2)
            self.assertFalse((root/'author.bin').exists())
            self.assertFalse((root/'reviewer.bin').exists())

    def test_cli_symlinked_parent_and_preexisting_output_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-signer-unsafe-') as folder:
            root=Path(folder);d,r,keys,prepare,assemble=cli_fixture(root)
            original=root/'author.bin';original.write_text('not yours')
            original.chmod(0o600)
            self.assertEqual(run_cli(prepare).returncode,2)
            self.assertEqual(original.read_text(),'not yours')
            self.assertFalse((root/'reviewer.bin').exists())
            original.unlink()
            link=root/'link';link.symlink_to(root,target_is_directory=True)
            args=list(prepare);args[args.index('--author-payload')+1]=str(link/'payload.bin')
            self.assertEqual(run_cli(args).returncode,2)
            self.assertFalse((root/'payload.bin').exists())
            self.assertFalse((root/'reviewer.bin').exists())

    def test_cli_rejects_signatures_with_extra_trailing_byte(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-signature-length-') as folder:
            root=Path(folder);d,r,keys,prepare,assemble=cli_fixture(root)
            self.assertEqual(run_cli(prepare).returncode,0)
            for role in ('author','reviewer'):
                private_file(root/(role+'.sig'),keys[role].sign((root/(role+'.bin')).read_bytes()))
            with (root/'author.sig').open('ab') as stream:
                stream.write(b'\0')
            result=run_cli(assemble)
            self.assertEqual(result.returncode,2)
            self.assertFalse((root/'prewitness.json').exists())
            self.assertFalse(json.loads(result.stdout)['production_authorized'])

    def test_preexisting_prewitness_never_overwritten_even_with_valid_signatures(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-signature-no-overwrite-') as folder:
            root=Path(folder);d,r,keys,prepare,assemble=cli_fixture(root)
            self.assertEqual(run_cli(prepare).returncode,0)
            for role in ('author','reviewer'):
                private_file(root/(role+'.sig'),keys[role].sign((root/(role+'.bin')).read_bytes()))
            out=root/'prewitness.json';out.write_bytes(b'protected independent handoff')
            out.chmod(0o600)
            bad=run_cli(assemble)
            self.assertEqual(bad.returncode,2)
            self.assertEqual(out.read_bytes(),b'protected independent handoff')

if __name__=='__main__':unittest.main()
