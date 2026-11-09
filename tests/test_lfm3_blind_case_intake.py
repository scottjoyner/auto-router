"""Synthetic-only role separated projections. All signing keys memory-only."""
from __future__ import annotations
import copy
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import re
import unittest

from research.lfm3_top5.blind_case_intake import (
    DATA_CLASS,SCHEMA,RECEIPT_SCHEMA,prepare_blind_packet
)
from research.lfm3_top5.independent_custody_handoff import (
    KEY_SCHEMA,COMMIT_SCHEMA,FROZEN_PREREG_SHA,SCHEMA as SIGN_SCHEMA,
    ROLES,role_payload
)
from research.lfm3_top5.prospective_holdout_gate import MODEL_SHA,sha
from tests.test_lfm3_independent_custody_handoff import fictional_cases,signed_synthetic


TEST_BLIND_KEY = bytes(range(32))  # synthetic-only; CLI creates random keys


def signed_safe():
    data=fictional_cases()
    # Existing synthetic fixtures deliberately leak 'category=<gold>'.
    # Remove those clues and RE-SIGN all 36 case+gold commitments.
    for c in data['cases']:
        c['note']=re.sub(r'category=[a-z_]+','fictional event descriptor',c['note'])
    keys={role:Ed25519PrivateKey.generate() for role in ROLES}
    roster={'schema':KEY_SCHEMA,'roles':{
        role:{'key_id':f'fictional.projection.{role}.20261109',
              'ed25519_public_key_hex':keys[role].public_key().public_bytes(
                  encoding=serialization.Encoding.Raw,
                  format=serialization.PublicFormat.Raw).hex()}
        for role in ROLES}}
    commitment={'schema':COMMIT_SCHEMA,'dataset_sha256':sha(data),
                'prereg_sha256':FROZEN_PREREG_SHA,
                'checkpoint_sha256':MODEL_SHA}
    bundle={'schema':SIGN_SCHEMA,'commitment':commitment,
            'claimed_external_anchor_reference':'synthetic:UNTRUSTED-CUSTODY-ONLY',
            'signatures':{}}
    for role in ROLES:
        bundle['signatures'][role]={
            'key_id':roster['roles'][role]['key_id'],
            'signature_hex':keys[role].sign(role_payload(
                role,commitment,bundle['signatures'],bundle['claimed_external_anchor_reference'])).hex()}
    return data,bundle,roster


class BlindProjection(unittest.TestCase):
    def invoke(self,inputs):
        return prepare_blind_packet(*inputs,declared_data_class=DATA_CLASS,
                                    blinding_secret=TEST_BLIND_KEY)

    def assert_hold(self,inputs,reason=None):
        out=self.invoke(inputs)
        self.assertEqual(out['decision'],'HOLD',out)
        self.assertFalse(out['permitted_to_infer'])
        self.assertFalse(out['production_authorized'])
        self.assertFalse(out['external_case_custody_verified'])
        self.assertNotIn('packet',out)
        self.assertNotIn('receipt',out)
        if reason:self.assertEqual(out['reason'],reason)
        return out

    def test_fictional_signed_gold_free_packet_is_structural_only(self):
        data,bundle,roster=signed_safe()
        out=self.invoke((data,bundle,roster))
        self.assertEqual(out['decision'],'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY',out)
        packet=out['packet'];receipt=out['receipt']
        self.assertEqual(packet['schema'],SCHEMA)
        self.assertEqual(receipt['schema'],RECEIPT_SCHEMA)
        self.assertEqual(packet['case_count'],36)
        self.assertEqual(receipt['source_dataset_sha256'],sha(data))
        self.assertEqual(receipt['blind_prompt_view_sha256'],sha(packet))
        aliases=[x['id'] for x in packet['cases']]
        self.assertEqual(aliases,sorted(aliases))
        self.assertEqual(len(set(aliases)),36)
        self.assertTrue(all(len(alias)==25 and alias.startswith('B') for alias in aliases))
        self.assertTrue(set(aliases).isdisjoint({x['id'] for x in data['cases']}))
        self.assertEqual(set(receipt['opaque_id_to_original_id']),set(aliases))
        self.assertEqual(set(receipt['opaque_id_to_original_id'].values()),
                         {x['id'] for x in data['cases']})
        self.assertTrue(all(set(case)=={'id','note'} for case in packet['cases']))
        self.assertNotIn('expected',set(packet))
        self.assertNotIn('challenges',json.dumps(packet))
        self.assertNotIn('source_dataset_sha256',json.dumps(packet))
        self.assertNotIn('opaque_id_to_original_id',json.dumps(packet))
        self.assertFalse(packet['permitted_to_infer'])
        self.assertFalse(receipt['permitted_to_infer'])
        self.assertFalse(out['permitted_to_infer'])
        self.assertFalse(out['production_authorized'])
        self.assertFalse(out['independent_author_verified'])
        self.assertFalse(out['trusted_key_ownership_verified'])
        self.assertFalse(out['model_value_verified'])

    def test_old_synthetic_fixtures_leak_gold_and_are_rejected(self):
        self.assert_hold(signed_synthetic(),'suspected_gold_label_leak_in_source_note')

    def test_random_blinding_secret_changes_visible_aliases_not_source_commitment(self):
        d,b,r=signed_safe()
        one=prepare_blind_packet(d,b,r,declared_data_class=DATA_CLASS,
                                 blinding_secret=TEST_BLIND_KEY)
        two=prepare_blind_packet(d,b,r,declared_data_class=DATA_CLASS,
                                 blinding_secret=bytes(reversed(TEST_BLIND_KEY)))
        self.assertEqual(one['decision'],'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY')
        self.assertEqual(two['decision'],'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY')
        self.assertEqual(one['receipt']['source_dataset_sha256'],
                         two['receipt']['source_dataset_sha256'])
        self.assertNotEqual(set(one['receipt']['opaque_id_to_original_id']),
                            set(two['receipt']['opaque_id_to_original_id']))
        self.assertNotEqual(one['receipt']['blind_prompt_view_sha256'],
                            two['receipt']['blind_prompt_view_sha256'])
        self.assertEqual(self.invoke((d,b,r))['packet'],one['packet'])

    def test_missing_or_weak_identifier_secret_holds(self):
        d,b,r=signed_safe()
        for secret in (None,True,{},[],0,b'',b'a'*31,b'a'*33,'a'*32):
            result=prepare_blind_packet(d,b,r,declared_data_class=DATA_CLASS,
                                        blinding_secret=secret)
            self.assertEqual(result['decision'],'HOLD')
            self.assertEqual(result['reason'],'missing_or_weak_opaque_identifier_secret')
            self.assertNotIn('packet',result)

    def test_gold_tampered_after_signatures_holds(self):
        data,bundle,roster=signed_safe()
        data['cases'][0]['expected']['category']='node_auth_contract'
        self.assert_hold((data,bundle,roster),'signature_case_or_prereg_commitment_mismatch')

    def test_note_tampered_after_signatures_holds(self):
        data,bundle,roster=signed_safe()
        data['cases'][0]['note']+=' injected source sentence.'
        self.assert_hold((data,bundle,roster),'signature_case_or_prereg_commitment_mismatch')

    def test_future_case_label_embedded_in_note_holds(self):
        data,bundle,roster=signed_safe()
        data['cases'][0]['note']+=' expected: node_auth_contract'
        self.assert_hold((data,bundle,roster))

    def test_unknown_data_class_or_private_data_holds(self):
        args=signed_safe()
        for data_class in ('private','production','real_incident','independently_heldout',None,True,0):
            res=prepare_blind_packet(*args,declared_data_class=data_class,
                                     blinding_secret=TEST_BLIND_KEY)
            self.assertEqual(res['decision'],'HOLD')
            self.assertEqual(res['reason'],'unapproved_or_unproven_data_class')
            self.assertNotIn('packet',res)

    def test_missing_or_fake_roster_and_signatures_holds(self):
        d,b,r=signed_safe()
        self.assert_hold((d,b,None),'signature_missing_nonoverlapping_three_role_key_roster')
        d,b,r=signed_safe()
        del b['signatures']['witness']
        self.assert_hold((d,b,r),'signature_missing_signature_or_unexpected_role')
        d,b,r=signed_safe()
        b['signatures']['author']['signature_hex']='0'*128
        self.assert_hold((d,b,r),'signature_invalid_signature_author')

    def test_reused_signer_pubkey_holds(self):
        d,b,r=signed_safe()
        r['roles']['reviewer']['ed25519_public_key_hex']=r['roles']['author']['ed25519_public_key_hex']
        self.assert_hold((d,b,r),'signature_missing_nonoverlapping_three_role_key_roster')

    def test_data_contract_duplicate_case_or_unbalanced_hold(self):
        d,b,r=signed_safe()
        d['cases'][1]['id']=d['cases'][0]['id']
        self.assert_hold((d,b,r),'signature_invalid_or_unbalanced_independent_case_contract')
        d,b,r=signed_safe()
        d['cases']=d['cases'][:-1]
        self.assert_hold((d,b,r),'signature_invalid_or_unbalanced_independent_case_contract')

    def test_projection_sorted_is_independent_of_case_file_order_only(self):
        d,b,r=signed_safe()
        old=self.invoke((d,b,r))
        # Changing dataset row order changes canonical source commitment, so
        # old signatures MUST HOLD; never silently canonicalize signed source.
        d['cases'].reverse()
        self.assert_hold((d,b,r),'signature_case_or_prereg_commitment_mismatch')
        # A newly signed reordered dataset may project to the same prompt view.
        d,b,r=signed_safe()
        d['cases'].reverse()
        # This test only ensures rejection above; re-signing is custodian work.
        self.assertEqual(old['packet']['case_count'],len(d['cases']))

    def test_forged_authority_field_holds(self):
        d,b,r=signed_safe()
        d['production_authorized']=True
        self.assert_hold((d,b,r),'signature_invalid_or_unbalanced_independent_case_contract')
        d,b,r=signed_safe()
        b['production_authorized']=True
        self.assert_hold((d,b,r),'signature_invalid_handoff_bundle')

    def test_non_object_invalid_input_holds_without_packet(self):
        d,b,r=signed_safe()
        for item in (None,False,{},[],0,'fictional'):
            self.assert_hold((item,b,r))

    def test_cli_writes_two_restricted_gold_free_files(self):
        data,bundle,roster=signed_safe()
        with tempfile.TemporaryDirectory(prefix='lfm3-blind-intake-fixture-') as loc:
            root=Path(loc)
            for label,part in (('dataset',data),('bundle',bundle),('roster',roster)):
                (root/(label+'.json')).write_text(json.dumps(part,sort_keys=True))
            packet=root/'note-only.json';receipt=root/'custodian-receipt.json'
            cmd=[sys.executable,'-m','scripts.lfm3_prepare_blind_packet',
                 '--dataset',str(root/'dataset.json'),
                 '--bundle',str(root/'bundle.json'),
                 '--key-roster',str(root/'roster.json'),
                 '--packet-output',str(packet),
                 '--receipt-output',str(receipt),
                 '--source-class',DATA_CLASS]
            good=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(good.returncode,0,good.stderr)
            body=json.loads(good.stdout)
            self.assertEqual(body['decision'],'PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY')
            self.assertFalse(body['permitted_to_infer'])
            self.assertFalse(body['production_authorized'])
            self.assertNotIn(data['cases'][0]['note'],good.stdout)
            self.assertNotIn('source_dataset_sha256',good.stdout)
            self.assertEqual(stat.S_IMODE(packet.stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode),0o600)
            note_view=json.loads(packet.read_text())
            hidden=json.loads(receipt.read_text())
            self.assertEqual(set(note_view['cases'][0]),{'id','note'})
            self.assertNotIn('source_dataset_sha256',note_view)
            self.assertEqual(hidden['source_dataset_sha256'],sha(data))
            self.assertEqual(set(hidden['opaque_id_to_original_id']),
                             {case['id'] for case in note_view['cases']})
            self.assertTrue(all(case['id'] not in note_view['cases'][0]['id']
                                for case in data['cases']))
            self.assertEqual(hidden['blind_prompt_view_sha256'],sha(note_view))
            self.assertNotIn('expected',set(note_view))
            self.assertFalse(note_view['permitted_to_infer'])
            self.assertFalse(hidden['permitted_to_infer'])
            repeat=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(repeat.returncode,2)
            self.assertEqual(json.loads(repeat.stdout)['reason'],
                             'output_already_exists_no_overwrite')
            self.assertEqual(json.loads(packet.read_text()),note_view)
            self.assertEqual(json.loads(receipt.read_text()),hidden)
            packet.unlink();receipt.unlink()
            (root/'dataset.json').write_text(json.dumps({**data,'production_authorized':True}))
            invalid=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(invalid.returncode,2)
            self.assertFalse(packet.exists())
            self.assertFalse(receipt.exists())

    def test_cli_bad_data_class_symlink_and_missing_file_hold(self):
        data,bundle,roster=signed_safe()
        with tempfile.TemporaryDirectory(prefix='lfm3-blind-intake-cli-') as loc:
            root=Path(loc)
            for label,part in (('dataset',data),('bundle',bundle),('roster',roster)):
                (root/(label+'.json')).write_text(json.dumps(part))
            packet=root/'view.json';receipt=root/'secret-receipt.json'
            cmd=[sys.executable,'-m','scripts.lfm3_prepare_blind_packet',
                 '--dataset',str(root/'dataset.json'),'--bundle',str(root/'bundle.json'),
                 '--key-roster',str(root/'roster.json'),
                 '--packet-output',str(packet),'--receipt-output',str(receipt),
                 '--source-class',DATA_CLASS]
            wrong=subprocess.run(cmd[:-1]+['private'],capture_output=True,text=True,timeout=15)
            self.assertEqual(wrong.returncode,2)
            self.assertFalse(packet.exists())
            target=root/'protected.json';target.write_text('do-not-overwrite')
            packet.symlink_to(target)
            symlink=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(symlink.returncode,2)
            self.assertEqual(target.read_text(),'do-not-overwrite')
            self.assertFalse(receipt.exists())
            packet.unlink()
            (root/'bundle.json').unlink()
            missing=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(missing.returncode,2)
            self.assertFalse(packet.exists())
            self.assertFalse(receipt.exists())

    def test_projection_has_no_network_or_inference_behavior(self):
        d,b,r=signed_safe()
        result=self.invoke((d,b,r))
        self.assertFalse(result['permitted_to_infer'])
        self.assertFalse(result['receipt']['external_case_custody_verified'])
        self.assertFalse(result['receipt']['pre_inference_seal_verified'])
        self.assertNotIn('model_answer',json.dumps(result['packet']))

if __name__=='__main__':unittest.main()
