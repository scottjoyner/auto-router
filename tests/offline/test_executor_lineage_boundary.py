"""Standalone executor-auth and claim-fence regressions.

Runs without importing the auto_router package/app and WITHOUT any network.
Uses a freshly generated ephemeral Ed25519 test key and ASGI fake requests.

Usage: python3 -m unittest discover -s tests/offline -p test_executor_lineage_boundary.py -v
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE=Path(__file__).resolve().parents[2]/"src/auto_router"

def module(name:str):
    sp=importlib.util.spec_from_file_location("isolated_"+name,BASE/(name+".py"))
    mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
    return mod

auth=module("executor_auth")
fence=module("claim_fence")

def segment(value):
    return base64.urlsafe_b64encode(json.dumps(value,sort_keys=True).encode()).rstrip(b"=").decode()

class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key=Ed25519PrivateKey.generate()
        pub=self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        self.env=patch.dict(os.environ,{
            "AUTO_ROUTER_EXECUTOR_VERIFY_KEY_PEM":pub,
            "AUTO_ROUTER_EXECUTOR_KEY_ID":"offline-test-only",
            "AUTO_ROUTER_EXECUTOR_AUTH_REQUIRED":"true",
        })
        self.env.start()
    async def asyncTearDown(self):
        self.env.stop()

    def token(self, *, task_id="task-A", claim_id="claim-A"):
        now=int(time.time())
        head=segment({"alg":"EdDSA","typ":"assistx-executor+jwt","kid":"offline-test-only"})
        payload=segment({
            "iss":"assistx","aud":["auto-router"],"iat":now,"nbf":now,
            "exp":now+60,"jti":"offline-"+str(time.time_ns()),
            "task_id":task_id,"claim_id":claim_id,"agent_id":"worker-A",
            "projection_generation":5,"scopes":["inference"],
            "allowed_model_aliases":["auto/code"],"max_input_tokens":500,
            "max_output_tokens":4,"max_attempts":1,
        })
        signature=self.key.sign((head+"."+payload).encode())
        return head+"."+payload+"."+base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    async def invoke(self, bearer, incoming):
        state=SimpleNamespace(runtime_projection_manager=SimpleNamespace(
            current=SimpleNamespace(generation=5,is_fresh=lambda:True)))
        seen={"status":None,"payload":None}
        sent=False
        async def receive():
            nonlocal sent
            if sent:return {"type":"http.disconnect"}
            sent=True
            return {"type":"http.request","body":json.dumps(incoming).encode(),
                    "more_body":False}
        async def downstream(scope, inner_receive, inner_send):
            body=await inner_receive()
            seen["payload"]=json.loads(body["body"])
            await inner_send({"type":"http.response.start","status":204,"headers":[]})
            await inner_send({"type":"http.response.body","body":b""})
        async def send(msg):
            if msg["type"]=="http.response.start":seen["status"]=msg["status"]
        scope={"type":"http","method":"POST","path":"/v1/chat/completions",
               "headers":[(b"authorization",("Bearer "+bearer).encode())]}
        await auth.ExecutorInferenceAuthMiddleware(downstream,state)(scope,receive,send)
        return seen

    def request(self, **metadata):
        return SimpleNamespace(metadata=metadata)

    def payload(self, metadata):
        return {"model":"auto/code","max_tokens":2,
                "messages":[{"role":"user","content":"offline"}],
                "metadata":metadata}

    async def test_malicious_service_marker_stripped_and_fence_required(self):
        result=await self.invoke(self.token(),self.payload({
            "assistx_service":{"authenticated":True,"identity":"assistx-internal"},
            "client_annotation":"safe",
        }))
        self.assertEqual(result["status"],204)
        meta=result["payload"]["metadata"]
        self.assertNotIn("assistx_service",meta)
        self.assertEqual(meta["client_annotation"],"safe")
        self.assertEqual(meta["assistx_executor"]["task_id"],"task-A")
        self.assertIsNotNone(fence._executor_metadata(self.request(**meta)))

    async def test_malicious_executor_marker_replaced(self):
        result=await self.invoke(self.token(),self.payload({
            "assistx_executor":{"task_id":"forged","claim_id":"forged"},
        }))
        self.assertEqual(result["status"],204)
        self.assertEqual(result["payload"]["metadata"]["assistx_executor"]["claim_id"],
                         "claim-A")

    async def test_internal_token_removes_caller_executor_marker(self):
        with patch.dict(os.environ,{"AUTO_ROUTER_INTERNAL_SERVICE_TOKEN":"offline-internal-only"}):
            result=await self.invoke("offline-internal-only",self.payload({
                "assistx_executor":{"task_id":"forged"},"client_annotation":"safe"
            }))
        self.assertEqual(result["status"],204)
        meta=result["payload"]["metadata"]
        self.assertNotIn("assistx_executor",meta)
        self.assertEqual(meta["assistx_service"]["identity"],"assistx-internal")
        self.assertIsNone(fence._executor_metadata(self.request(**meta)))

    async def test_dual_markers_cannot_select_service_bypass(self):
        with self.assertRaisesRegex(fence.ExecutorClaimFenceError,"conflicting"):
            fence._executor_metadata(self.request(
                assistx_service={"identity":"assistx-internal","authenticated":True},
                assistx_executor={"task_id":"task-A"}
            ))

    async def test_unverified_service_marker_cannot_skip_claim_fence(self):
        with self.assertRaisesRegex(fence.ExecutorClaimFenceError,"lineage is missing"):
            fence._executor_metadata(self.request(
                assistx_service={"authenticated":True}
            ))

    async def test_client_marker_cannot_bypass_revoked_claim_check(self):
        """Prove the post-auth fence actually consults revoked claim state."""
        result=await self.invoke(self.token(),self.payload({
            "assistx_service":{"identity":"assistx-internal","authenticated":True},
            "client_annotation":"retained",
        }))
        self.assertEqual(result["status"],204)
        metadata=result["payload"]["metadata"]
        called={"count":0}

        class FakeResponse:
            status_code=200
            def json(self):
                return {"active":False,"reason":"revoked_offline_test"}

        class FakeClient:
            def __init__(self,**kwargs):
                self.timeout=kwargs.get("timeout")
            async def __aenter__(self):return self
            async def __aexit__(self,*_):return False
            async def get(self,url,headers=None):
                called["count"]+=1
                assert url.endswith("/api/executor/claims/task-A/status")
                assert headers=={"Authorization":"Bearer offline-status-only"}
                return FakeResponse()

        state=SimpleNamespace(runtime_projection_manager=SimpleNamespace(
            current=SimpleNamespace(generation=5,is_fresh=lambda:True)))
        with patch.dict(os.environ,{
            "AUTO_ROUTER_EXECUTOR_CLAIM_STATUS_URL":"http://localhost.invalid",
            "AUTO_ROUTER_ASSISTX_EXECUTOR_SERVICE_TOKEN":"offline-status-only",
        }),patch.object(fence.httpx,"AsyncClient",FakeClient):
            with self.assertRaisesRegex(fence.ExecutorClaimFenceError,
                                        "revoked_offline_test"):
                await fence.assert_executor_claim_current(
                    self.request(**metadata),state)
        self.assertEqual(called["count"],1)

    async def test_two_synthetic_signed_claims_cannot_swap_payload_lineage(self):
        """Two unique signed tasks cannot inherit spoofed client claim IDs."""
        a=await self.invoke(self.token(task_id="task-A",claim_id="claim-A"),
                            self.payload({"assistx_executor":{
                                "task_id":"task-B","claim_id":"claim-B"
                            }}))
        b=await self.invoke(self.token(task_id="task-B",claim_id="claim-B"),
                            self.payload({"assistx_executor":{
                                "task_id":"task-A","claim_id":"claim-A"
                            }}))
        self.assertEqual([a["status"],b["status"]],[204,204])
        meta_a=a["payload"]["metadata"]
        meta_b=b["payload"]["metadata"]
        self.assertEqual(meta_a["assistx_executor"]["task_id"],"task-A")
        self.assertEqual(meta_a["assistx_executor"]["claim_id"],"claim-A")
        self.assertEqual(meta_b["assistx_executor"]["task_id"],"task-B")
        self.assertEqual(meta_b["assistx_executor"]["claim_id"],"claim-B")
        self.assertNotEqual(meta_a["assistx_executor"]["token_jti"],
                            meta_b["assistx_executor"]["token_jti"])
        self.assertIsNotNone(fence._executor_metadata(self.request(**meta_a)))
        self.assertIsNotNone(fence._executor_metadata(self.request(**meta_b)))

    async def test_missing_bearer_denied_without_downstream_execution(self):
        result=await self.invoke("",self.payload({"assistx_service":{
            "identity":"assistx-internal","authenticated":True
        }}))
        self.assertEqual(result["status"],401)
        self.assertIsNone(result["payload"])

if __name__=="__main__":
    unittest.main()
