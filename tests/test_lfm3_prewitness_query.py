"""Memory-only synthetic authors/reviewers; local OpenSSL, zero external TSA."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from research.lfm3_top5.independent_custody_handoff import (
    COMMIT_SCHEMA, KEY_SCHEMA, SCHEMA as FINAL_SCHEMA, FROZEN_PREREG_SHA,
    ROLES, role_payload,
)
from research.lfm3_top5.prospective_holdout_gate import MODEL_SHA
from scripts.lfm3_rfc3161_anchor_verify import verify_timestamp, TOKEN_PREFIX
from tests.test_lfm3_rfc3161_anchor_verifier import certificate_fixture, execute
from research.lfm3_top5.prewitness_commitment import (
    SCHEMA, PRE_ROLES, inspect_prewitness
)
from research.lfm3_top5.prospective_holdout_gate import canonical, sha
from scripts.lfm3_prepare_prewitness_tsq import prepare_prewitness_query
from scripts.lfm3_rfc3161_anchor_verify import (
    OPENSSL, _query_imprint_is_expected, _single_der_sequence
)
from tests.test_lfm3_independent_custody_handoff import signed_synthetic


def fixture():
    dataset,full,roster=signed_synthetic()
    pre={
        'schema':SCHEMA,
        'commitment':copy.deepcopy(full['commitment']),
        'signatures':{role:copy.deepcopy(full['signatures'][role]) for role in PRE_ROLES},
    }
    return dataset,pre,roster


def prepare_cli_fixture(root):
    data,pre,roster=fixture()
    files={'dataset':data,'prewitness':pre,'key-roster':roster}
    args=[sys.executable,'-m','scripts.lfm3_prepare_prewitness_tsq']
    for key,value in files.items():
        path=root/(key+'.json')
        path.write_text(json.dumps(value,sort_keys=True))
        path.chmod(0o600)
        args.extend(['--'+key,str(path)])
    output=root/'dataset-commitment.tsq'
    args.extend(['--tsq-output',str(output)])
    return args,output,files


def cli(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=15)


class PreWitnessQueryContract(unittest.TestCase):
    def check_hold(self,data,pre,roster,reason=None):
        out=inspect_prewitness(data,pre,roster)
        self.assertEqual(out['decision'],'HOLD',out)
        for key in ('independent_author_verified','independent_gold_reviewer_verified',
                    'witness_signature_verified','trusted_key_ownership_verified',
                    'external_timestamp_token_received','pre_inference_seal_verified',
                    'permitted_to_infer','model_value_verified','production_authorized'):
            self.assertFalse(out[key])
        if reason:self.assertEqual(out['reason'],reason)
        return out

    def test_two_real_ed25519_role_signatures_verify_without_external_identity(self):
        data,pre,roster=fixture()
        output=inspect_prewitness(data,pre,roster)
        self.assertEqual(output['decision'],'PASS_OFFLINE_TWO_ROLE_COMMITMENT_CRYPTO_ONLY')
        self.assertTrue(output['offline_author_and_reviewer_signatures_verified'])
        self.assertEqual(output['dataset_commitment_imprint_sha256'],
                         hashlib.sha256(canonical(pre['commitment']).encode()).hexdigest())
        self.assertNotIn(data['cases'][0]['note'],json.dumps(output))
        self.assertNotIn(data['cases'][0]['id'],json.dumps(output))
        self.assertNotIn(pre['commitment']['dataset_sha256'],json.dumps(output))
        for field in ('independent_author_verified','independent_gold_reviewer_verified',
                      'witness_signature_verified','trusted_key_ownership_verified',
                      'external_timestamp_token_received','pre_inference_seal_verified',
                      'permitted_to_infer','production_authorized'):
            self.assertFalse(output[field],field)

    def test_actual_openssl_nonce_query_matches_signed_commitment_imprint(self):
        data,pre,roster=fixture()
        result,query=prepare_prewitness_query(data,pre,roster)
        self.assertEqual(result['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY',result)
        self.assertTrue(result['query_nonce_present'])
        self.assertFalse(result['external_timestamp_token_received'])
        self.assertFalse(result['permitted_to_infer'])
        self.assertIsInstance(query,bytes)
        self.assertTrue(_single_der_sequence(query))
        self.assertEqual(hashlib.sha256(query).hexdigest(),result['timestamp_query_sha256'])
        with tempfile.TemporaryDirectory(prefix='lfm3-tsq-inspect-') as folder:
            path=Path(folder)/'req.tsq';path.write_bytes(query)
            x=subprocess.run([OPENSSL,'ts','-query','-in',str(path),'-text'],
                             capture_output=True,text=True,timeout=10)
            self.assertEqual(x.returncode,0,x.stderr)
            self.assertTrue(_query_imprint_is_expected(x.stdout,
                hashlib.sha256(canonical(pre['commitment']).encode()).hexdigest()))

    def test_new_query_bridges_to_final_witness_signature_after_synthetic_tsa(self):
        # An entirely local self-created TSA+three memory-only signers verifies
        # the pre-witness -> TSQ -> TSR -> final witness handshake structure.
        # It is NOT trusted issuer identity or actual sealed source custody.
        data=signed_synthetic()[0]
        keys={role:Ed25519PrivateKey.generate() for role in ROLES}
        roster={'schema':KEY_SCHEMA,'roles':{
            role:{'key_id':f'fictional.twophase.{role}.20261109',
                  'ed25519_public_key_hex':keys[role].public_key().public_bytes(
                      encoding=serialization.Encoding.Raw,
                      format=serialization.PublicFormat.Raw).hex()}
            for role in ROLES}}
        commit={'schema':COMMIT_SCHEMA,'dataset_sha256':sha(data),
                'prereg_sha256':FROZEN_PREREG_SHA,
                'checkpoint_sha256':MODEL_SHA}
        sigs={role:{
            'key_id':roster['roles'][role]['key_id'],
            'signature_hex':keys[role].sign(role_payload(
                role,commit,{},'unused-before-timestamp')).hex()}
            for role in PRE_ROLES}
        pre={'schema':SCHEMA,'commitment':commit,'signatures':copy.deepcopy(sigs)}
        first,query=prepare_prewitness_query(data,pre,roster)
        self.assertEqual(first['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY')
        self.assertFalse(first['witness_signature_verified'])
        with tempfile.TemporaryDirectory(prefix='lfm3-synthetic-two-phase-') as folder:
            root=Path(folder)
            imprint=hashlib.sha256(canonical(commit).encode()).hexdigest()
            _q,_t,ca,tsa=certificate_fixture(root,imprint)
            q=root/'query-from-prewitness.tsq';q.write_bytes(query)
            token_file=root/'response-from-prewitness.tsr'
            execute([OPENSSL,'ts','-reply','-queryfile',str(q),
                     '-signer',str(root/'synthetic-tsa.pem'),
                     '-inkey',str(root/'synthetic-tsa.key'),
                     '-chain',str(root/'synthetic-ca.pem'),
                     '-out',str(token_file),'-tspolicy','1.2.3.4.1'])
            token=token_file.read_bytes()
            reference=TOKEN_PREFIX+hashlib.sha256(token).hexdigest()
            sigs['witness']={
                'key_id':roster['roles']['witness']['key_id'],
                'signature_hex':keys['witness'].sign(role_payload(
                    'witness',commit,sigs,reference)).hex()}
            bundle={'schema':FINAL_SCHEMA,'commitment':commit,
                    'signatures':sigs,'claimed_external_anchor_reference':reference}
            out=verify_timestamp(data,bundle,roster,query,token,ca,tsa,
                                 hashlib.sha256(ca).hexdigest())
            self.assertEqual(out['decision'],'PASS_OFFLINE_TIMESTAMP_CRYPTO_ONLY',out)
            self.assertTrue(out['witness_signature_bound_to_token'])
            self.assertTrue(out['timestamp_query_bound_to_dataset'])
            self.assertFalse(out['issuer_independently_authenticated'])
            self.assertFalse(out['pre_inference_chronology_verified'])
            self.assertFalse(out['external_custody_verified'])
            self.assertFalse(out['production_authorized'])

    def test_query_nonce_is_new_each_time_not_replayed_or_deterministic(self):
        d,p,r=fixture()
        a,qa=prepare_prewitness_query(d,p,r)
        b,qb=prepare_prewitness_query(d,p,r)
        self.assertEqual(a['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY')
        self.assertEqual(b['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY')
        self.assertNotEqual(qa,qb)
        self.assertNotEqual(a['timestamp_query_sha256'],b['timestamp_query_sha256'])

    def test_gold_and_note_changed_after_author_sign_holds(self):
        d,p,r=fixture()
        d['cases'][0]['expected']['category']='node_auth_contract'
        self.check_hold(d,p,r,'private_corpus_or_frozen_commitment_mismatch')
        d,p,r=fixture();d['cases'][0]['note']+=' Tampered'
        self.check_hold(d,p,r,'private_corpus_or_frozen_commitment_mismatch')

    def test_bad_role_signatures_and_swapped_signatures_hold(self):
        for role in PRE_ROLES:
            d,p,r=fixture();p['signatures'][role]['signature_hex']='0'*128
            self.check_hold(d,p,r,'invalid_'+role+'_signature')
        d,p,r=fixture();p['signatures']['author']=p['signatures']['reviewer']
        self.check_hold(d,p,r,'untrusted_or_missing_author_signature')

    def test_requires_distinct_three_role_public_roster_even_before_witness(self):
        d,p,r=fixture();r['roles']['witness']['ed25519_public_key_hex']=r['roles']['author']['ed25519_public_key_hex']
        self.check_hold(d,p,r,'missing_or_reused_three_role_public_roster')
        d,p,r=fixture();r['roles']['reviewer']['key_id']=r['roles']['author']['key_id']
        self.check_hold(d,p,r,'missing_or_reused_three_role_public_roster')

    def test_witness_signature_and_claimed_anchor_prematurely_inserted_holds(self):
        d,p,r=fixture();p['signatures']['witness']={'key_id':'fake','signature_hex':'0'*128}
        self.check_hold(d,p,r,'witness_must_sign_only_after_external_token')
        d,p,r=fixture();p['claimed_external_anchor_reference']='https://invalid.example/claim'
        self.check_hold(d,p,r,'incorrect_or_extra_prewitness_fields')
        d,p,r=fixture();p['production_authorized']=True
        self.check_hold(d,p,r,'incorrect_or_extra_prewitness_fields')

    def test_frozen_checkpoint_prereg_and_case_balance_holds(self):
        d,p,r=fixture();p['commitment']['checkpoint_sha256']='f'*64
        self.check_hold(d,p,r,'private_corpus_or_frozen_commitment_mismatch')
        d,p,r=fixture();p['commitment']['prereg_sha256']='f'*64
        self.check_hold(d,p,r,'private_corpus_or_frozen_commitment_mismatch')
        d,p,r=fixture();d['cases']=d['cases'][:25]
        self.check_hold(d,p,r,'invalid_or_unbalanced_private_case_contract')

    def test_unknown_roster_invalid_bundle_and_missing_cases_hold(self):
        d,p,r=fixture()
        for val in (None,[],True,{},'not-a-dataset'):
            self.check_hold(val,p,r)
            self.check_hold(d,val,r)
            self.check_hold(d,p,val)

    def test_actual_cli_writes_private_der_query_without_raw_case_disclosure(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-prewitness-cli-') as folder:
            root=Path(folder);args,out,data=prepare_cli_fixture(root)
            result=cli(args)
            self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
            public=json.loads(result.stdout)
            self.assertEqual(public['decision'],'PASS_OFFLINE_UNSIGNED_TIMESTAMP_QUERY_ONLY')
            self.assertFalse(public['external_timestamp_token_received'])
            self.assertFalse(public['production_authorized'])
            self.assertNotIn(data['dataset']['cases'][0]['note'],result.stdout)
            self.assertNotIn(data['prewitness']['commitment']['dataset_sha256'],result.stdout)
            self.assertTrue(out.exists())
            self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o600)
            self.assertTrue(_single_der_sequence(out.read_bytes()))
            again=cli(args)
            self.assertEqual(again.returncode,2)
            self.assertEqual(json.loads(again.stdout)['decision'],'HOLD')
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(),public['timestamp_query_sha256'])

    def test_cli_bad_input_modes_symlink_parent_and_absent_input_hold(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-prewitness-cli-neg-') as folder:
            root=Path(folder);args,out,_=prepare_cli_fixture(root)
            source=root/'dataset.json';source.chmod(0o644)
            self.assertEqual(cli(args).returncode,2)
            self.assertFalse(out.exists())
            source.chmod(0o600)
            parent=root/'alias';parent.symlink_to(root,target_is_directory=True)
            replaced=list(args);i=replaced.index('--dataset')+1;replaced[i]=str(parent/'dataset.json')
            self.assertEqual(cli(replaced).returncode,2)
            self.assertFalse(out.exists())
            source.unlink()
            self.assertEqual(cli(args).returncode,2)
            self.assertFalse(out.exists())

    def test_cli_unsafe_output_parent_or_preexisting_query_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-prewitness-out-') as folder:
            root=Path(folder);args,out,_=prepare_cli_fixture(root)
            root.chmod(0o755)
            self.assertEqual(cli(args).returncode,2)
            self.assertFalse(out.exists())
            root.chmod(0o700)
            out.write_bytes(b'never-overwrite');out.chmod(0o600)
            self.assertEqual(cli(args).returncode,2)
            self.assertEqual(out.read_bytes(),b'never-overwrite')
            out.unlink()
            self.assertEqual(cli(args).returncode,0)
            self.assertTrue(_single_der_sequence(out.read_bytes()))

    def test_cli_bad_query_file_final_symlink_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-prewitness-query-symlink-') as folder:
            root=Path(folder);args,out,_=prepare_cli_fixture(root)
            target=root/'protected';target.write_bytes(b'protected');target.chmod(0o600)
            out.symlink_to(target)
            self.assertEqual(cli(args).returncode,2)
            self.assertEqual(target.read_bytes(),b'protected')
            self.assertTrue(out.is_symlink())

if __name__=='__main__':unittest.main()
