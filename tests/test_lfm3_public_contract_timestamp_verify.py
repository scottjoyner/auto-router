"""Real provider-issued PUBLIC contract timestamp; no secret data or source trust claim."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.lfm3_public_contract_timestamp_verify import verify_public_anchor

RECEIPT=Path(__file__).resolve().parents[1]/'research/lfm3_top5/public_contract_anchor/receipt.json'

def sample():
    return json.loads(RECEIPT.read_text())

class PublicContractTimestamp(unittest.TestCase):
    def check_hold(self,record,prefix=None):
        result=verify_public_anchor(record)
        self.assertEqual(result['decision'],'HOLD',result)
        self.assertFalse(result['production_authorized'])
        self.assertFalse(result['model_value_verified'])
        self.assertFalse(result['independent_case_dataset_sealed'])
        self.assertFalse(result['provider_identity_independently_authenticated'])
        self.assertFalse(result['pre_inference_case_custody_verified'])
        if prefix:self.assertTrue(result['reason'].startswith(prefix),result['reason'])

    def test_real_provider_issued_public_contract_receipt(self):
        result=verify_public_anchor(sample())
        self.assertEqual(result['decision'],'PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY',result)
        self.assertEqual(result['public_contract_sha256'],
                         '1f9a6bd4129a104e8e1846ed1a4c3727fc0f4834cb58353f877801dd833803a2')
        self.assertEqual(result['provider_claimed_issued_utc'],'2026-11-09T14:34:22Z')
        for item in ('provider_token_signature_verified','public_contract_digest_bound',
                     'original_query_nonce_verified','provider_ca_self_published_fingerprint_matches',
                     'provider_crl_signature_verified','signer_unrevoked_in_provider_crl_at_token_time'):
            self.assertTrue(result[item],item)
        for item in ('provider_identity_independently_authenticated',
                     'independent_case_dataset_sealed','pre_inference_case_custody_verified',
                     'earlier_development_runs_retroactively_validated','provider_crl_independently_trusted',
                     'model_value_verified',
                     'production_authorized'):
            self.assertFalse(result[item],item)

    def test_no_private_case_corpus_exists_in_receipt(self):
        record=sample();c=record['contract']
        self.assertIsNone(c['case_dataset_sha256'])
        self.assertFalse(record['independent_case_dataset_sealed'])
        self.assertFalse(record['pre_inference_custody_verified'])
        self.assertEqual(set(c),frozenset((
            'schema','scope','repo','source_head','source_tree','preregistration_sha256',
            'baseline_rule_sha256','prospective_scorer_sha256',
            'checkpoint_expected_sha256','case_dataset_sha256',
            'independent_author_present','independent_gold_reviewer_present',
            'external_case_custody_verified','model_value_verified','production_authorized')))
        self.assertNotIn('private_key',RECEIPT.read_text().lower())

    def test_tampered_public_contract_rejected(self):
        for key,val in (
            ('source_head','f'*40),('preregistration_sha256','0'*64),
            ('checkpoint_expected_sha256','0'*64),('case_dataset_sha256','0'*64),
            ('production_authorized',True),('independent_gold_reviewer_present',True)):
            x=sample();x['contract'][key]=val
            self.check_hold(x,'source_contract_')

    def test_forged_outer_production_authority_rejected(self):
        x=sample();x['production_authorized']=True
        self.check_hold(x,'nonpublic_scope')
        x=sample();x['independent_case_dataset_sealed']=True
        self.check_hold(x,'nonpublic_scope')
        x=sample();x['real_case_data']='not allowed'
        self.check_hold(x,'unexpected_public_receipt_shape')

    def test_modified_token_and_nonce_fail(self):
        for key in ('timestamp_response_der_base64','timestamp_query_der_base64'):
            x=sample()
            raw=bytearray(base64.b64decode(x[key]));raw[-10]^=1
            x[key]=base64.b64encode(raw).decode()
            self.check_hold(x,'raw_public_receipt_material_digest_mismatch')

    def test_wrong_published_root_or_token_sha_rejected(self):
        for key in ('provider_published_ca_pem_sha256','provider_published_tsa_pem_sha256',
                    'timestamp_query_sha256','timestamp_response_sha256',
                    'provider_root_crl_pem_sha256'):
            x=sample();x[key]='0'*64
            self.check_hold(x,'frozen_timestamp_or_provider_certificate_pin_mismatch')

    def test_swapped_ca_or_signer_blob_rejected(self):
        x=sample();x['tsa_ca_pem_base64']=x['tsa_signer_pem_base64']
        self.check_hold(x,'raw_public_receipt_material_digest_mismatch')
        x=sample();x['tsa_signer_pem_base64']=x['tsa_ca_pem_base64']
        self.check_hold(x,'raw_public_receipt_material_digest_mismatch')

    def test_bad_base64_oversize_and_der_rejected(self):
        for bad in ('**bad**','AAA','',None,True,'A'*1000000):
            x=sample();x['timestamp_query_der_base64']=bad
            self.check_hold(x,'malformed_or_oversized_public_receipt_material')
        x=sample();x['timestamp_query_der_base64']=base64.b64encode(b'\x30\x80').decode()
        self.check_hold(x,'timestamp_query_or_reply_not_single_der_sequence')

    def test_signed_crl_snapshot_is_bound_but_not_independent_trust(self):
        receipt=sample()
        self.assertEqual(receipt['provider_crl_last_update_utc'],'2026-09-18T10:30:55Z')
        self.assertEqual(receipt['provider_crl_next_update_utc'],'2027-09-18T10:30:55Z')
        self.assertEqual(receipt['provider_tsa_signer_serial_hex'],'C2E986160DA8E9CD')
        result=verify_public_anchor(receipt)
        self.assertTrue(result['provider_crl_signature_verified'])
        self.assertTrue(result['signer_unrevoked_in_provider_crl_at_token_time'])
        self.assertFalse(result['provider_crl_independently_trusted'])
        self.assertFalse(result['provider_identity_independently_authenticated'])

    def test_crl_byte_tampering_or_wrong_source_holds(self):
        record=sample()
        raw=bytearray(base64.b64decode(record['provider_root_crl_pem_base64']))
        raw[-14]^=1
        record['provider_root_crl_pem_base64']=base64.b64encode(raw).decode()
        self.check_hold(record,'raw_public_receipt_material_digest_mismatch')
        record=sample()
        record['provider_published_crl_url']='https://example.invalid/fake.crl'
        self.check_hold(record,'nonpublic_scope_or_claimed_release_authority')
        record=sample()
        record['provider_root_crl_pem_base64']='AAA'
        self.check_hold(record,'malformed_or_oversized_public_receipt_material')

    def test_crl_validity_and_signer_serial_claim_tampering_holds(self):
        for key in ('provider_crl_last_update_utc','provider_crl_next_update_utc',
                    'provider_tsa_signer_serial_hex'):
            record=sample();record[key]='not-the-signed-value'
            self.check_hold(record,'provider_signed_time_claim_mismatch')
        record=sample();record['provider_crl_independently_trusted']=True
        self.check_hold(record,'nonpublic_scope_or_claimed_release_authority')

    def test_changed_signed_timestamp_claim_rejected(self):
        x=sample();x['tsa_claimed_utc_time']='2026-11-08T14:34:22Z'
        self.check_hold(x,'provider_signed_time_claim_mismatch')

    def test_invalid_receipt_input_types_fail_closed(self):
        for value in (None,[],{},True,'hello',{'schema':'bad'}):
            self.check_hold(value)

    def test_real_cli_pass_and_missing_or_tampered_file_hold(self):
        cmd=[sys.executable,'-m','scripts.lfm3_public_contract_timestamp_verify']
        with tempfile.TemporaryDirectory(prefix='lfm3-public-ts-test-') as location:
            path=Path(location)/'receipt.json';path.write_bytes(RECEIPT.read_bytes())
            args=cmd+['--receipt',str(path)]
            good=subprocess.run(args,text=True,capture_output=True,timeout=15)
            self.assertEqual(good.returncode,0,good.stderr)
            body=json.loads(good.stdout)
            self.assertEqual(body['decision'],'PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY')
            self.assertFalse(body['production_authorized'])
            x=sample();x['contract']['case_dataset_sha256']='0'*64
            path.write_text(json.dumps(x,sort_keys=True))
            bad=subprocess.run(args,text=True,capture_output=True,timeout=15)
            self.assertEqual(bad.returncode,2)
            self.assertEqual(json.loads(bad.stdout)['decision'],'HOLD')
            path.unlink()
            missing=subprocess.run(args,text=True,capture_output=True,timeout=15)
            self.assertEqual(missing.returncode,2)
            self.assertEqual(json.loads(missing.stdout)['decision'],'HOLD')

    def test_duplicate_json_keys_in_cli_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-public-receipt-') as location:
            path=Path(location)/'bad.json'
            path.write_text('{"schema":"bad","schema":"bad"}')
            result=subprocess.run([sys.executable,'-m',
                'scripts.lfm3_public_contract_timestamp_verify','--receipt',str(path)],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout)['decision'],'HOLD')

    def test_repeated_verification_offline_does_not_mutate_signed_receipt(self):
        before=hashlib.sha256(RECEIPT.read_bytes()).hexdigest()
        for _ in range(2):
            self.assertEqual(verify_public_anchor(sample())['decision'],
                             'PASS_PROVIDER_PUBLIC_CONTRACT_TIMESTAMP_CRYPTO_ONLY')
        self.assertEqual(hashlib.sha256(RECEIPT.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main()
