"""Only fabricated fictional case records, in-memory signing and offline files."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from research.lfm3_top5.private_pair_integrity import inspect_private_pair
from research.lfm3_top5.prospective_holdout_gate import sha
from scripts.lfm3_audit_private_projection_pair import audit_private_paths
from scripts.lfm3_private_json_reader import read_private_json
from tests.test_lfm3_blind_case_intake import private_cli_fixture,run_private_cli


def setup_private_pair(root):
    export,packet,receipt,data=private_cli_fixture(root)
    result=run_private_cli(export)
    assert result.returncode==0,(result.stdout,result.stderr)
    return packet,receipt,data


def audit_cli(packet,receipt):
    cmd=[sys.executable,'-m','scripts.lfm3_audit_private_projection_pair',
         '--packet',str(packet),'--receipt',str(receipt)]
    return subprocess.run(cmd,capture_output=True,text=True,timeout=10)


class PrivatePairLinkage(unittest.TestCase):
    def test_complete_pair_passes_structural_only_and_never_grants_authority(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-good-') as folder:
            packet,receipt,data=setup_private_pair(Path(folder))
            run=audit_cli(packet,receipt)
            self.assertEqual(run.returncode,0,run.stderr)
            d=json.loads(run.stdout)
            self.assertEqual(d['decision'],'PASS_LOCAL_PAIR_LINKAGE_ONLY')
            self.assertTrue(d['private_pair_structure_verified'])
            self.assertEqual(d['blind_prompt_view_sha256'],sha(json.loads(packet.read_text())))
            self.assertEqual(d['case_count'],36)
            for key in ('export_transaction_completion_verified',
                        'original_signed_dataset_verified', 'trusted_key_ownership_verified',
                        'external_case_custody_verified','pre_inference_seal_verified',
                        'actual_model_execution_verified','model_value_verified',
                        'permitted_to_infer','production_authorized'):
                self.assertIs(d[key],False,key)
            self.assertNotIn('opaque_id_to_original_id',run.stdout)
            self.assertNotIn(data['cases'][0]['id'],run.stdout)
            self.assertNotIn(data['cases'][0]['note'],run.stdout)
            self.assertEqual(stat.S_IMODE(packet.stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode),0o600)

    def assert_paths_hold(self,packet,receipt):
        r=audit_cli(packet,receipt)
        self.assertEqual(r.returncode,2,(r.stdout,r.stderr))
        obj=json.loads(r.stdout)
        self.assertEqual(obj['decision'],'HOLD')
        self.assertFalse(obj['production_authorized'])
        self.assertFalse(obj['permitted_to_infer'])
        self.assertFalse(obj['export_transaction_completion_verified'])
        self.assertNotIn('opaque_id_to_original_id',r.stdout)
        return obj

    def test_crash_or_partial_export_missing_one_or_both_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-partial-') as folder:
            root=Path(folder);packet,receipt,_=setup_private_pair(root)
            packet.unlink();self.assert_paths_hold(packet,receipt)
            receipt.unlink();self.assert_paths_hold(packet,receipt)
            packet.write_bytes(b'{"schema":')
            packet.chmod(0o600)
            self.assert_paths_hold(packet,receipt)

    def test_receipt_missing_but_packet_valid_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-packet-only-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            receipt.unlink()
            self.assert_paths_hold(packet,receipt)
            self.assertTrue(packet.exists())

    def test_tamper_packet_but_preserve_receipt_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-mismatch-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            raw=json.loads(packet.read_text())
            raw['cases'][0]['note']+=' This is an additional entirely fictional observation.'
            packet.write_text(json.dumps(raw,sort_keys=True))
            self.assertEqual(self.assert_paths_hold(packet,receipt)['reason'],
                             'private_receipt_linkage_or_authority_mismatch')

    def test_tamper_receipt_mapping_duplicate_and_missing_alias_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-map-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            original=receipt.read_bytes();r=json.loads(original)
            ids=list(r['opaque_id_to_original_id'])
            for mutation in ('duplicate_original','missing_alias','forged_source_hash'):
                x=copy.deepcopy(r)
                if mutation=='duplicate_original':
                    x['opaque_id_to_original_id'][ids[0]]=x['opaque_id_to_original_id'][ids[1]]
                elif mutation=='missing_alias':
                    x['opaque_id_to_original_id'].pop(ids[0])
                else:
                    x['source_dataset_sha256']='Z'*64
                receipt.write_text(json.dumps(x,sort_keys=True))
                self.assert_paths_hold(packet,receipt)
                receipt.write_bytes(original)
            self.assertEqual(audit_cli(packet,receipt).returncode,0)

    def test_forged_release_or_untrusted_data_class_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-spoof-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            original_packet=packet.read_bytes();original_receipt=receipt.read_bytes()
            for key in ('production_authorized','permitted_to_infer','external_custody_verified'):
                x=json.loads(original_packet);x[key]=True
                packet.write_text(json.dumps(x))
                self.assert_paths_hold(packet,receipt)
            packet.write_bytes(original_packet)
            for key in ('production_authorized','permitted_to_infer','external_case_custody_verified'):
                x=json.loads(original_receipt);x[key]=True
                receipt.write_text(json.dumps(x))
                self.assert_paths_hold(packet,receipt)
            receipt.write_bytes(original_receipt)
            x=json.loads(original_packet);x['data_class_self_declared']='real_incident'
            packet.write_text(json.dumps(x))
            self.assert_paths_hold(packet,receipt)

    def test_obvious_answer_leak_and_extra_gold_field_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-gold-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            original=packet.read_bytes();x=json.loads(original)
            x['cases'][0]['note']+=' Expected: node_auth_contract.'
            packet.write_text(json.dumps(x))
            self.assertEqual(self.assert_paths_hold(packet,receipt)['reason'],
                             'obvious_gold_hint_in_model_facing_note')
            x=json.loads(original);x['cases'][0]['expected']={'category':'node_auth_contract'}
            packet.write_text(json.dumps(x))
            self.assertEqual(self.assert_paths_hold(packet,receipt)['reason'],
                             'unexpected_gold_or_extra_model_facing_field')

    def test_fully_coherent_forgery_does_not_claim_source_authenticity(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-forgery-') as folder:
            packet,receipt,_=setup_private_pair(Path(folder))
            x=json.loads(packet.read_text());r=json.loads(receipt.read_text())
            x['cases'][0]['note']+=' Additional synthetic no-gold description.'
            r['blind_prompt_view_sha256']=sha(x)
            packet.write_text(json.dumps(x,sort_keys=True))
            receipt.write_text(json.dumps(r,sort_keys=True))
            d=json.loads(audit_cli(packet,receipt).stdout)
            self.assertEqual(d['decision'],'PASS_LOCAL_PAIR_LINKAGE_ONLY')
            self.assertFalse(d['original_signed_dataset_verified'])
            self.assertFalse(d['export_transaction_completion_verified'])
            self.assertFalse(d['permitted_to_infer'])
            self.assertFalse(d['production_authorized'])
            # A mutually consistent forgery is NOT an independent-source proof.

    def test_symlinked_private_parent_rejected_even_if_real_files_are_valid(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-parent-') as folder:
            root=Path(folder);real=root/'real';real.mkdir(mode=0o700)
            packet,receipt,_=setup_private_pair(real)
            link=root/'alias';link.symlink_to(real,target_is_directory=True)
            self.assert_paths_hold(link/packet.name,receipt)
            self.assert_paths_hold(packet,link/receipt.name)
            self.assertEqual(audit_cli(packet,receipt).returncode,0)

    def test_symlink_hardlink_and_fifo_final_files_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-links-') as folder:
            root=Path(folder);packet,receipt,_=setup_private_pair(root)
            orig=packet.read_bytes();packet.rename(root/'source')
            packet.symlink_to(root/'source')
            self.assert_paths_hold(packet,receipt)
            packet.unlink();(root/'source').rename(packet)
            other=root/'hardlink';os.link(packet,other)
            self.assert_paths_hold(packet,receipt)
            other.unlink()
            packet.unlink();os.mkfifo(packet,0o600)
            self.assert_paths_hold(packet,receipt)
            packet.unlink();packet.write_bytes(orig);packet.chmod(0o600)
            self.assertEqual(audit_cli(packet,receipt).returncode,0)

    def test_shared_parent_or_world_readable_pair_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-perms-') as folder:
            root=Path(folder);packet,receipt,_=setup_private_pair(root)
            for path in (packet,receipt):
                path.chmod(0o644)
                self.assert_paths_hold(packet,receipt)
                path.chmod(0o600)
            root.chmod(0o755)
            self.assert_paths_hold(packet,receipt)
            root.chmod(0o700)
            self.assertEqual(audit_cli(packet,receipt).returncode,0)

    def test_oversized_and_noncanonical_or_duplicate_json_holds(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-json-') as folder:
            root=Path(folder);packet,receipt,_=setup_private_pair(root)
            before=packet.read_bytes()
            for raw in (b'',b'{"schema":"x","schema":"x"}',b'{"value":NaN}',b'garbled'):
                packet.write_bytes(raw)
                self.assert_paths_hold(packet,receipt)
            packet.write_bytes(before)
            with packet.open('ab') as f:
                f.write(b' '*(2*1024*1024+1-len(before)))
            self.assert_paths_hold(packet,receipt)
            packet.write_bytes(before)
            self.assertEqual(audit_cli(packet,receipt).returncode,0)

    def test_distinct_owner_private_parent_directories_pass(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-dirs-') as folder:
            root=Path(folder);left=root/'left';right=root/'right'
            left.mkdir(mode=0o700);right.mkdir(mode=0o700)
            packet,receipt,_=setup_private_pair(left)
            receipt.rename(right/receipt.name)
            receipt=right/receipt.name
            self.assertEqual(audit_cli(packet,receipt).returncode,0)
            self.assertFalse(json.loads(audit_cli(packet,receipt).stdout)['production_authorized'])

    def test_same_file_path_rejected_and_reader_dirfd_cannot_escape(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-pair-same-') as folder:
            root=Path(folder);packet,receipt,_=setup_private_pair(root)
            self.assertEqual(self.assert_paths_hold(packet,packet)['reason'],
                             'same_file_used_as_packet_and_receipt')
            directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                for value in ('../x','/tmp/x','.', '..'):
                    with self.subTest(path=value),self.assertRaises(ValueError):
                        read_private_json(value,limit=300,dir_fd=directory)
                self.assertEqual(read_private_json(packet.name,limit=2*1024*1024,
                                                   dir_fd=directory)['schema'],
                                 'lfm3.prospective.blind-case-packet.v1')
            finally:
                os.close(directory)

    def test_invalid_pure_objects_fail_without_leaking_notes(self):
        for value in (None,True,[],{},'text',12):
            result=inspect_private_pair(value,{'schema':'bad'})
            self.assertEqual(result['decision'],'HOLD')
            self.assertFalse(result['permitted_to_infer'])
            self.assertNotIn('cases',result)

if __name__=='__main__':
    unittest.main()
