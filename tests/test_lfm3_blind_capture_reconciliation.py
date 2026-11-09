"""Custodian-only opaque capture reattachment: fictional in-memory keys only."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research.lfm3_top5.blind_case_intake import DATA_CLASS,prepare_blind_packet
from research.lfm3_top5.blind_capture_reconciliation import SCHEMA,reconcile_blind_capture
from research.lfm3_top5.prospective_holdout_gate import MODEL_SHA,sha
from tests.test_lfm3_blind_case_intake import signed_safe,TEST_BLIND_KEY
from tests.test_lfm3_session_stability_gate import fixture

PREREG=Path(__file__).resolve().parents[1]/'research/lfm3_top5/prospective_prereg_v1.json'


def package():
    data,bundle,roster=signed_safe()
    x=prepare_blind_packet(data,bundle,roster,declared_data_class=DATA_CLASS,
                           blinding_secret=TEST_BLIND_KEY)
    assert x['decision']=='PASS_OFFLINE_GOLD_FREE_PROJECTION_ONLY'
    packet,receipt=x['packet'],x['receipt']
    lookup={old:alias for alias,old in receipt['opaque_id_to_original_id'].items()}
    _unused,_unused_hash,sample=fixture()
    for stage in sample['stages']:
        for src,dst in zip(data['cases'],stage['requests']):
            dst['case_id']=lookup[src['id']]
            dst['source_note_sha256']=sha(src['note'])
            answer=json.dumps(src['expected'],sort_keys=True)
            dst['response']=answer
            dst['response_sha256']=hashlib.sha256(answer.encode()).hexdigest()
    blinded={
        'schema':SCHEMA,'blind_prompt_view_sha256':sha(packet),
        'checkpoint_sha256':MODEL_SHA,'stages':sample['stages'],
        'production_authorized':False,'independent_gpu_attested':False,
    }
    return json.loads(PREREG.read_text()),data,bundle,roster,packet,receipt,blinded


class BlindCaptureReconciliation(unittest.TestCase):
    def test_full_four_stage_fictional_capture_reconstructs_without_exposing_ids(self):
        args=package()
        out=reconcile_blind_capture(*args)
        self.assertEqual(out['decision'],'PASS_OFFLINE_BLIND_CAPTURE_RECONCILIATION_ONLY',out)
        self.assertEqual(out['unique_case_count'],36)
        self.assertEqual(out['four_stage_observations'],144)
        self.assertEqual(out['primary_scored_observations'],72)
        self.assertEqual(out['mode_exact_counts'],{'cpu':36,'vulkan8':36})
        for key in ('blind_alias_mapping_verified','four_stage_capture_verified','frozen_scoring_verified'):
            self.assertTrue(out[key])
        for key in ('external_case_custody_verified','pre_inference_seal_verified',
                    'independent_author_verified','actual_model_execution_verified',
                    'model_value_verified','permitted_to_infer','production_authorized'):
            self.assertFalse(out[key])
        for note in args[1]['cases']:
            self.assertNotIn(note['note'],json.dumps(out))
            self.assertNotIn(note['id'],json.dumps(out))
        self.assertNotIn('opaque_id_to_original_id',out)

    def assert_hold(self,args,prefix=None):
        out=reconcile_blind_capture(*args)
        self.assertEqual(out['decision'],'HOLD',out)
        for key in ('external_case_custody_verified','actual_model_execution_verified',
                    'model_value_verified','permitted_to_infer','production_authorized'):
            self.assertFalse(out[key])
        if prefix:self.assertTrue(out['reason'].startswith(prefix),out['reason'])
        self.assertNotIn('mode_exact_counts',out)
        return out

    def test_missing_or_replaced_signed_source_holds(self):
        p=list(package());p[1]['cases'][0]['expected']['category']='node_auth_contract'
        self.assert_hold(p,'signed_handoff_')
        p=list(package());p[2]['signatures']['witness']['signature_hex']='0'*128
        self.assert_hold(p,'signed_handoff_')
        p=list(package());p[3]=None
        self.assert_hold(p,'signed_handoff_')

    def test_note_or_original_id_map_tampering_holds(self):
        p=list(package());p[4]['cases'][0]['note']+=' altered'
        self.assert_hold(p,'blind_receipt_hash_or_authority_mismatch')
        p=list(package());key=next(iter(p[5]['opaque_id_to_original_id']))
        p[5]['opaque_id_to_original_id'][key]='OTHER_NONEXISTENT'
        self.assert_hold(p,'unknown_repeated_or_missing_original_case_id')

    def test_alias_swaps_even_with_recomputed_receipt_packet_hash_hold(self):
        p=list(package())
        p[4]['cases'][0]['id'],p[4]['cases'][1]['id']=(
            p[4]['cases'][1]['id'],p[4]['cases'][0]['id'])
        p[5]['blind_prompt_view_sha256']=sha(p[4])
        self.assert_hold(p)

    def test_capture_prompt_hash_mismatch_holds(self):
        p=list(package());p[6]['blind_prompt_view_sha256']='0'*64
        self.assert_hold(p,'capture_opaque_prompt_identity_or_authority')

    def test_capture_uses_unknown_or_original_case_id_holds(self):
        p=list(package());p[6]['stages'][0]['requests'][0]['case_id']='ORIGINAL123'
        self.assert_hold(p,'unknown_opaque_case_id')

    def test_duplicate_blind_id_or_missing_stage_holds(self):
        p=list(package());p[6]['stages'][0]['requests'][1]['case_id']=(
            p[6]['stages'][0]['requests'][0]['case_id'])
        self.assert_hold(p,'frozen_reconciliation_')
        p=list(package());p[6]['stages'].pop()
        self.assert_hold(p,'four_blinded_stages_required')

    def test_replayed_request_id_and_wrong_mode_holds(self):
        p=list(package());p[6]['stages'][3]['requests'][0]['request_id']=(
            p[6]['stages'][0]['requests'][0]['request_id'])
        self.assert_hold(p,'frozen_reconciliation_')
        p=list(package());p[6]['stages'][1]['mode']='cpu'
        self.assert_hold(p,'frozen_reconciliation_')

    def test_bad_cleanup_or_unsafe_identity_claims_hold(self):
        p=list(package());p[6]['stages'][0]['child_stopped']=False
        self.assert_hold(p,'frozen_reconciliation_')
        p=list(package());p[6]['production_authorized']=True
        self.assert_hold(p,'capture_opaque_prompt_identity_or_authority')
        p=list(package());p[4]['permitted_to_infer']=True
        self.assert_hold(p,'malformed_or_unauthorized_blind_packet')
        p=list(package());p[5]['permitted_to_infer']=True
        self.assert_hold(p,'blind_receipt_hash_or_authority_mismatch')

    def test_source_note_sha_or_response_tamper_holds(self):
        p=list(package());p[6]['stages'][0]['requests'][0]['source_note_sha256']='0'*64
        self.assert_hold(p,'frozen_reconciliation_')
        p=list(package());p[6]['stages'][0]['requests'][0]['response']='tamp'
        self.assert_hold(p,'frozen_reconciliation_')

    def test_bad_shapes_extra_gold_fields_and_missing_inputs_hold(self):
        p=list(package());p[4]['cases'][0]['expected']={'category':'node_auth_contract','evidence':'E1'}
        p[5]['blind_prompt_view_sha256']=sha(p[4])
        self.assert_hold(p,'malformed_model_facing_rows')
        p=list(package());p[5]['original_gold']={'unexpected':'data'}
        self.assert_hold(p,'blind_receipt_hash_or_authority_mismatch')
        for i in range(7):
            p=list(package());p[i]=None
            self.assert_hold(p)

    def test_actual_cli_subprocess_redacts_private_ids_and_holds_if_modified(self):
        p=package()
        with tempfile.TemporaryDirectory(prefix='lfm3-blind-reconcile-') as d:
            root=Path(d)
            names=['manifest','dataset','bundle','key-roster','packet','receipt','capture']
            for name,content in zip(names,p):
                (root/(name+'.json')).write_text(json.dumps(content,sort_keys=True))
            cmd=[sys.executable,'-m','research.lfm3_top5.blind_capture_reconciliation']
            for name in names:cmd.extend(['--'+name,str(root/(name+'.json'))])
            good=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            self.assertEqual(good.returncode,0,good.stderr)
            self.assertEqual(json.loads(good.stdout)['decision'],
                             'PASS_OFFLINE_BLIND_CAPTURE_RECONCILIATION_ONLY')
            for source in p[1]['cases']:
                self.assertNotIn(source['id'],good.stdout)
                self.assertNotIn(source['note'],good.stdout)
            p[6]['stages'][2]['requests'][0]['request_id']='REPLAY-SAME'
            p[6]['stages'][3]['requests'][0]['request_id']='REPLAY-SAME'
            (root/'capture.json').write_text(json.dumps(p[6],sort_keys=True))
            bad=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            self.assertEqual(bad.returncode,2,bad.stderr)
            self.assertEqual(json.loads(bad.stdout)['decision'],'HOLD')
            (root/'bundle.json').unlink()
            absent=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            self.assertEqual(absent.returncode,2)
            self.assertEqual(json.loads(absent.stdout)['decision'],'HOLD')

if __name__=='__main__':unittest.main()
