"""Synthetic offline key-control challenge; no independent human enrollment."""
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

from research.lfm3_top5.key_possession_contract import (
    SCHEMA,PURPOSE,DOMAIN,issue_local_challenge,
    challenge_signing_bytes,verify_detached_key_possession,
)
from research.lfm3_top5.prospective_holdout_gate import canonical,sha
from tests.test_lfm3_signer_payload_handoff import fixtures,private_file


def run(cmd):
    return subprocess.run(cmd,capture_output=True,text=True,timeout=15)


class KeyPossession(unittest.TestCase):
    def assert_hold(self,roster,role,challenge,signature):
        result=verify_detached_key_possession(roster,role,challenge,signature)
        self.assertEqual(result['decision'],'HOLD',result)
        self.assertIs(result['signature_matches_supplied_public_key'],False)
        self.assertIs(result['independent_human_identity_verified'],False)
        self.assertIs(result['permitted_to_infer'],False)
        self.assertIs(result['production_authorized'],False)
        return result

    def test_each_distinct_role_can_prove_supplied_public_key_control_only(self):
        _,roster,keys=fixtures()
        fingerprints=set()
        for role in ('author','reviewer','witness'):
            status,c=issue_local_challenge(roster,role)
            self.assertEqual(status['decision'],'PASS_OFFLINE_CHALLENGE_CREATED_ONLY')
            self.assertTrue(status['challenge_shape_verified'])
            self.assertFalse(status['independent_challenge_issuance_verified'])
            self.assertEqual(c['schema'],SCHEMA)
            self.assertEqual(c['purpose'],PURPOSE)
            self.assertEqual(c['role'],role)
            fingerprints.add(c['ed25519_public_key_sha256'])
            prep,payload=challenge_signing_bytes(roster,role,c)
            self.assertEqual(prep['decision'],'PASS_STATIC_KEY_CHALLENGE_BYTES_ONLY')
            self.assertTrue(payload.startswith(DOMAIN))
            self.assertEqual(payload[len(DOMAIN):],canonical(c).encode())
            sig=keys[role].sign(payload)
            result=verify_detached_key_possession(roster,role,c,sig)
            self.assertEqual(result['decision'],'PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY')
            self.assertTrue(result['signature_matches_supplied_public_key'])
            for forbidden in ('independent_challenge_issuance_verified',
                             'challenge_freshness_and_single_use_verified',
                             'independent_human_identity_verified',
                             'signer_organizational_independence_verified',
                             'pre_inference_seal_verified',
                             'permitted_to_infer','production_authorized'):
                self.assertFalse(result[forbidden])
            self.assertNotIn(c['nonce_hex'],json.dumps(result))
            self.assertNotIn(roster['roles'][role]['ed25519_public_key_hex'],json.dumps(result))
        self.assertEqual(len(fingerprints),3)

    def test_new_nonces_unpredictable_and_never_reported_as_single_use(self):
        _,roster,keys=fixtures()
        a,c=issue_local_challenge(roster,'author')
        b,d=issue_local_challenge(roster,'author')
        self.assertNotEqual(c['nonce_hex'],d['nonce_hex'])
        self.assertNotEqual(sha(c),sha(d))
        _,payload=challenge_signing_bytes(roster,'author',c)
        sig=keys['author'].sign(payload)
        first=verify_detached_key_possession(roster,'author',c,sig)
        replay=verify_detached_key_possession(roster,'author',c,sig)
        self.assertEqual(first['decision'],replay['decision'])
        self.assertFalse(replay['challenge_freshness_and_single_use_verified'])
        self.assert_hold(roster,'author',d,sig)

    def test_wrong_signer_role_cross_witness_or_modified_nonce_holds(self):
        _,r,keys=fixtures()
        _,c=issue_local_challenge(r,'author')
        _,p=challenge_signing_bytes(r,'author',c)
        signature=keys['author'].sign(p)
        self.assert_hold(r,'reviewer',c,signature)
        self.assert_hold(r,'witness',c,signature)
        self.assert_hold(r,'author',c,keys['reviewer'].sign(p))
        alt=copy.deepcopy(c);alt['nonce_hex']='0'*64
        self.assert_hold(r,'author',alt,signature)
        alt=copy.deepcopy(c);alt['key_id']=r['roles']['reviewer']['key_id']
        self.assert_hold(r,'author',alt,signature)
        alt=copy.deepcopy(c);alt['role']='reviewer'
        self.assert_hold(r,'author',alt,signature)

    def test_wrong_checkpoint_domain_and_authority_fields_hold(self):
        _,r,keys=fixtures()
        _,c=issue_local_challenge(r,'reviewer')
        _,p=challenge_signing_bytes(r,'reviewer',c)
        sig=keys['reviewer'].sign(p)
        for key,val in (
            ('checkpoint_sha256','a'*64),
            ('prereg_sha256','b'*64),
            ('ed25519_public_key_sha256','c'*64),
            ('purpose','production_release'),
            ('independent_identity_verified',True),
            ('production_authorized',True),
            ('permitted_to_infer',True),
            ('nonce_hex','Z'*64),
            ('nonce_hex','1'*63),
            ('schema','future-v2'),
        ):
            with self.subTest(key=key):
                changed=copy.deepcopy(c);changed[key]=val
                self.assert_hold(r,'reviewer',changed,sig)
        changed=copy.deepcopy(c);changed['external_issuer_verified']=True
        self.assert_hold(r,'reviewer',changed,sig)

    def test_missing_reused_public_keys_and_bad_inputs_hold(self):
        _,r,_=fixtures()
        for role in ('unknown', '',None,True,[],{}):
            with self.subTest(role=repr(role)):
                status,c=issue_local_challenge(r,role)
                self.assertEqual(status['decision'],'HOLD');self.assertIsNone(c)
        altered=copy.deepcopy(r)
        altered['roles']['witness']['ed25519_public_key_hex']=altered['roles']['author']['ed25519_public_key_hex']
        status,c=issue_local_challenge(altered,'author')
        self.assertEqual(status['decision'],'HOLD');self.assertIsNone(c)
        for roster in (None,{},True,[],{'bad':1}):
            status,c=issue_local_challenge(roster,'author')
            self.assertEqual(status['decision'],'HOLD');self.assertIsNone(c)

    def test_bad_signature_sizes_and_nonbytes_hold(self):
        _,r,keys=fixtures()
        _,c=issue_local_challenge(r,'author')
        _,p=challenge_signing_bytes(r,'author',c)
        sig=keys['author'].sign(p)
        for val in (sig+b'x',sig[:-1],sig.hex(),None,True,[],b''):
            with self.subTest(value=repr(val)[:20]):self.assert_hold(r,'author',c,val)

    def test_actual_cli_openssl_sign_and_verify_for_all_three_roles(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-key-control-') as folder:
            root=Path(folder);_,roster,keys=fixtures()
            private_file(root/'roster.json',roster)
            for role in ('author','reviewer','witness'):
                chal=root/(role+'.json');payload=root/(role+'.bin');sig=root/(role+'.sig')
                prepare=[sys.executable,'-m','scripts.lfm3_key_possession_challenge','prepare',
                    '--key-roster',str(root/'roster.json'),'--role',role,
                    '--challenge-output',str(chal),'--payload-output',str(payload)]
                before=run(prepare)
                self.assertEqual(before.returncode,0,(before.stdout,before.stderr))
                self.assertEqual(json.loads(before.stdout)['decision'],
                                 'PASS_OFFLINE_CHALLENGE_CREATED_ONLY')
                self.assertEqual(stat.S_IMODE(chal.stat().st_mode),0o600)
                self.assertEqual(stat.S_IMODE(payload.stat().st_mode),0o600)
                keyfile=root/(role+'.test-private.pem')
                private_file(keyfile,keys[role].private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()))
                signed=run(['openssl','pkeyutl','-sign','-rawin','-inkey',str(keyfile),
                            '-in',str(payload),'-out',str(sig)])
                self.assertEqual(signed.returncode,0,signed.stderr)
                keyfile.unlink()
                self.assertEqual(sig.stat().st_size,64)
                sig.chmod(0o600)
                check=[sys.executable,'-m','scripts.lfm3_key_possession_challenge','verify',
                       '--key-roster',str(root/'roster.json'),'--role',role,
                       '--challenge',str(chal),'--signature',str(sig)]
                result=run(check)
                self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
                body=json.loads(result.stdout)
                self.assertEqual(body['decision'],'PASS_OFFLINE_KEY_SIGNATURE_STRUCTURE_ONLY')
                self.assertFalse(body['independent_human_identity_verified'])
                self.assertFalse(body['permitted_to_infer'])
                self.assertEqual(run(prepare).returncode,2)

    def test_private_input_and_output_permissions_reject_unsafe_files(self):
        with tempfile.TemporaryDirectory(prefix='lfm3-key-control-modes-') as folder:
            root=Path(folder);_,r,_=fixtures();roster=root/'roster.json'
            private_file(roster,r)
            prepare=[sys.executable,'-m','scripts.lfm3_key_possession_challenge','prepare',
                     '--key-roster',str(roster),'--role','author',
                     '--challenge-output',str(root/'c.json'),
                     '--payload-output',str(root/'s.bin')]
            roster.chmod(0o644)
            self.assertEqual(run(prepare).returncode,2)
            self.assertFalse((root/'c.json').exists())
            roster.chmod(0o600)
            outside=root/'outside';outside.mkdir(mode=0o700)
            link=root/'link';link.symlink_to(outside,target_is_directory=True)
            argv=list(prepare);argv[argv.index('--payload-output')+1]=str(link/'s.bin')
            self.assertEqual(run(argv).returncode,2)
            self.assertFalse((root/'c.json').exists())
            self.assertFalse((outside/'s.bin').exists())
            self.assertEqual(run(prepare).returncode,0)
            c=root/'c.json';s=root/'s.bin'
            c.chmod(0o644)
            sig=root/'sig';private_file(sig,b'X'*64)
            cmd=[sys.executable,'-m','scripts.lfm3_key_possession_challenge','verify',
                 '--key-roster',str(roster),'--role','author',
                 '--challenge',str(c),'--signature',str(sig)]
            self.assertEqual(run(cmd).returncode,2)
            c.chmod(0o600)
            sig.chmod(0o644)
            self.assertEqual(run(cmd).returncode,2)
            sig.chmod(0o600)
            self.assertEqual(run(cmd).returncode,2) # wrong detached proof
            self.assertTrue(c.exists() and s.exists())

if __name__=='__main__':unittest.main()
