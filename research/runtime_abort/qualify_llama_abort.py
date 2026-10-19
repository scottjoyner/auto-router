#!/usr/bin/env python3
"""Explicit, isolated REAL llama.cpp HTTP stream-close witness.

No production endpoint, loaded service, model placement, routing, or user data
is modified. The canary lives only on x1 loopback and a dedicated port.

This proves at most that a stream close frees this runtime's serving slot and
permits a new request. It is not a per-token epoch, GPU cancellation or cross-
host failover proof; authorization is synthetic and the model is a local 1B.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import tempfile
import time

import httpx

HOST="x1-370"
PORT=12741
BINARY=Path("/home/scott/.lmstudio/extensions/backends/llama.cpp-linux-x86_64-vulkan-avx2-2.60.0/llama-server")
MODEL=Path("/home/scott/.lmstudio/models/IFM/K2-Horizon-0.9B-GGUF/K2-Horizon-1B-BF16.gguf")
ROOT=Path(__file__).resolve().parent


def _resident_resources() -> dict[str,float]:
    meminfo={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("MemAvailable:","SwapFree:")):
            name,amount,*_=line.split()
            meminfo[name.rstrip(":")]=int(amount)
    psi=(Path("/proc/pressure/memory").read_text().splitlines()[1])
    avg10=float(next(x.split("=")[1] for x in psi.split() if x.startswith("avg10=")))
    return {"memory_available_gib":round(meminfo["MemAvailable"]/1048576,2),
            "swap_free_gib":round(meminfo["SwapFree"]/1048576,2),
            "memory_full_avg10":avg10}


def _check_preflight()->dict:
    # Check the actual research integration imports BEFORE launching the
    # expensive model subprocess. Some hosts' system Python lacks router
    # dependencies (for example pydantic_settings) even though the router
    # project venv is ready. Missing libraries must fail before startup.
    import importlib.util
    for name in ("httpx", "pydantic_settings", "pydantic", "cryptography"):
        if importlib.util.find_spec(name) is None:
            raise RuntimeError(
                f"NO_GO missing {name}; run via the existing Auto-Router virtualenv"
            )
    if socket.gethostname()!=HOST:
        raise RuntimeError("NO_GO host mismatch: x1 physical canary only")
    if not BINARY.is_file() or not os.access(BINARY,os.X_OK) or not MODEL.is_file():
        raise RuntimeError("NO_GO approved binary or model missing")
    if MODEL.stat().st_size<100_000_000 or MODEL.stat().st_size>2_500_000_000:
        raise RuntimeError("NO_GO model size outside bounded canary")
    resources=_resident_resources()
    if resources["memory_available_gib"]<12 or resources["memory_full_avg10"]>=.30:
        raise RuntimeError("NO_GO x1 available memory or PSI gate")
    with socket.socket() as sock:
        sock.settimeout(.25)
        if sock.connect_ex(("127.0.0.1",PORT))==0:
            raise RuntimeError("NO_GO dedicated canary port already occupied")
    return resources


def _limit_process():
    # Bound canary VA to 12 GiB; model is approx. 2.1 GiB, CPU-only.
    resource.setrlimit(resource.RLIMIT_AS,(12*1024**3,12*1024**3))
    os.nice(10)


def _request(client:httpx.Client,path:str,method="GET",**kw)->httpx.Response:
    r=client.request(method,path,**kw)
    r.raise_for_status()
    return r


def _slots(client:httpx.Client)->list[dict]:
    result=_request(client,"/slots").json()
    if not isinstance(result,list):
        raise RuntimeError("NO_GO slot telemetry not available as a list")
    return result


def _busy(slots:list[dict])->bool:
    return any(bool(s.get("is_processing")) for s in slots)


def _wait_idle(client:httpx.Client, timeout_s:float)->tuple[float,list[dict]]:
    started=time.monotonic()
    last=[]
    while time.monotonic()-started<timeout_s:
        last=_slots(client)
        if last and not _busy(last):
            return round(time.monotonic()-started,3),last
        time.sleep(.15)
    raise RuntimeError("NO_GO serving slot did not become idle after stream abort")


def _stream_first(client:httpx.Client)->dict:
    # A bounded, non-sensitive synthetic counting task. We never wait for a
    # full 1024-token stream; close after the first server-side data chunks.
    prompt="Write sequential positive integers starting at 1, separated by commas, without explanation. Continue for a long time:\n1, 2, 3,"
    payload={"prompt":prompt,"stream":True,"n_predict":1024,
             "temperature":0,"cache_prompt":False,"stop":[]}
    count=0
    seen_bytes=0
    first_elapsed=None
    began=time.monotonic()
    with client.stream("POST","/completion",json=payload,timeout=httpx.Timeout(15.0)) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            event=json.loads(line[6:])
            if not isinstance(event,dict) or "content" not in event:
                continue
            seen_bytes+=len(str(event["content"]).encode())
            count+=1
            if first_elapsed is None:
                first_elapsed=round(time.monotonic()-began,3)
            if count>=3:
                # Explicit exit closes HTTP response but does not stop
                # the serving process. Slot telemetry must show it releases.
                break
    return {"received_stream_events_before_abort":count,
            "synthetic_content_bytes_observed":seen_bytes,
            "first_event_s":first_elapsed,
            "http_stream_closed_after_events":True}


def _fenced_real_model_stream() -> dict:
    """Real llama.cpp provider HTTP stream through default-off router wrapper.

    The authority is synthetic and only lives inside this isolated canary
    process. It does not issue a signed execution epoch or authorize real
    production routing.
    """
    import asyncio
    import sys

    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/"src"))
    sys.path.insert(0,str(root/"tests"))
    from auto_router.models import ProviderConfig, RouterRequest
    from auto_router.providers import OpenAICompatibleProvider
    from auto_router.stream_effect_fence import StreamEffectDenied
    from test_stream_lifecycle import _module, _request, _candidate

    class RevokingTestAuthority:
        def __init__(self):
            self.sequences=[]
            self.preflights=0
            self.aborts=0
            self.slot_busy_when_revoked=None
        async def preflight(self,*,request,candidate):
            self.preflights+=1
            return True
        async def accept_chunk(self,*,request,candidate,sequence,chunk_sha256):
            if type(chunk_sha256) is not str or len(chunk_sha256)!=64:
                raise AssertionError("chunk digest not supplied")
            self.sequences.append(sequence)
            if sequence==4:
                # Observe actual runtime activity at the instant our fake
                # authority denies the next HTTP chunk. This distinguishes
                # aborting an active generation from natural EOS completion.
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{PORT}",
                    timeout=2.0,trust_env=False
                ) as observer:
                    response=await observer.get("/slots")
                    response.raise_for_status()
                    self.slot_busy_when_revoked=_busy(response.json())
            return sequence<=3
        async def abort(self,*,request):
            self.aborts+=1
            return True

    async def execute() -> dict:
        cfg=ProviderConfig(
            id="isolated-llama-canary",
            name="isolated-llama-canary",
            type="openai",
            base_url=f"http://127.0.0.1:{PORT}/v1",
            models=[],
        )
        provider=OpenAICompatibleProvider(cfg,timeout_seconds=15)
        synthetic_prompt=(
            "Write sequential positive integers separated by commas, "
            "starting at 1 and continuing for a long time."
        )
        actual_request=RouterRequest(
            request_id="isolated-runtime-epoch-revoke-only",
            route="completions",
            model="research-1b-abort-only",
            stream=True,
            raw_body={
                "model":"research-1b-abort-only",
                "prompt":synthetic_prompt,
                "stream":True,
                "max_tokens":512,
                "temperature":0,
            },
        )
        async def actual_llama_body():
            original=await provider.stream_completions(
                actual_request,"research-1b-abort-only"
            )
            try:
                async for raw_chunk in original.body:
                    yield raw_chunk
            finally:
                await original.body.aclose()

        module,_,_,policy,circuits,quota,idempotency,outbox=_module(actual_llama_body)
        owner=module._owner(_candidate())
        authority=RevokingTestAuthority()
        module.state.stream_effect_authority=authority
        wrapper_request=_request()
        wrapper_request.route="completions"
        wrapper_request.request_id="isolated-runtime-epoch-revoke-only"
        accepted=[]
        import os
        old_stream=os.environ.get("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE")
        old_owner=os.environ.get("AUTO_ROUTER_REQUIRE_OWNER_BINDING")
        os.environ["AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE"]="true"
        os.environ["AUTO_ROUTER_REQUIRE_OWNER_BINDING"]="true"
        try:
            response=await module._dispatch_stream(
                None,_candidate(),wrapper_request
            )
            blocked=False
            try:
                async for chunk in response.body:
                    accepted.append(chunk)
            except StreamEffectDenied:
                blocked=True
            if not blocked:
                raise RuntimeError("NO_GO real model stream not fenced after synthetic revoke")
        finally:
            for key,previous in (
                ("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE",old_stream),
                ("AUTO_ROUTER_REQUIRE_OWNER_BINDING",old_owner),
            ):
                if previous is None:os.environ.pop(key,None)
                else:os.environ[key]=previous
        if len(accepted)!=3 or authority.sequences!=[1,2,3,4]:
            raise RuntimeError("NO_GO real provider stream chunk count or fence mismatch")
        if authority.preflights!=1 or authority.aborts!=1:
            raise RuntimeError("NO_GO router did not preflight or issue one bounded abort")
        if authority.slot_busy_when_revoked is not True:
            raise RuntimeError("NO_GO model already idle when fake epoch was revoked")
        if not outbox.events or outbox.events[-1].event_type!="router.stream.failed":
            raise RuntimeError("NO_GO router recorded revoked real stream as completed")
        if outbox.events[-1].payload["chunks_sent"]!=3:
            raise RuntimeError("NO_GO revoked output leaked into usage accounting")
        return {
            "real_provider_adapter":"OpenAICompatibleProvider.stream_completions",
            "real_router_wrapper":"stream_lifecycle.install_stream_lifecycle",
            "real_stream_chunks_released_before_synthetic_revocation":len(accepted),
            "real_stream_chunk_four_suppressed":True,
            "real_model_slot_was_processing_at_revocation":True,
            "trusted_epoch_authority_in_test":False,
            "synthetic_authority_preflight_calls":authority.preflights,
            "synthetic_authority_chunk_attempts":authority.sequences,
            "synthetic_authority_abort_calls":authority.aborts,
            "router_final_state":"possibly_accepted",
            "real_http_stream_closed_by_adapter":True,
        }
    return asyncio.run(execute())


def run()->dict:
    resources=_check_preflight()
    args=[str(BINARY),"--model",str(MODEL),"--host","127.0.0.1",
          "--port",str(PORT),"--gpu-layers","0","--threads","2",
          "--threads-batch","2","--parallel","1","--ctx-size","2048",
          "--no-webui","--metrics","--slots","--timeout","25",
          "--alias","research-1b-abort-only"]
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix="router-llama-abort-") as dir:
        private=Path(dir)
        os.chmod(private,0o700)
        logfile=private/"canary.log"
        env={**os.environ,"OMP_NUM_THREADS":"2"}
        with logfile.open("w",encoding="utf-8") as log:
            server=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,
                                    start_new_session=True,env=env,preexec_fn=_limit_process)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{PORT}",timeout=5,
                              trust_env=False) as client:
                ready=False
                for _ in range(140):
                    if server.poll() is not None:
                        details=logfile.read_text(errors="replace")[-1400:]
                        raise RuntimeError(
                            "NO_GO dedicated llama-server exited before ready: "+
                            details.replace(str(MODEL),"<approved_model>")
                        )
                    try:
                        r=client.get("/health")
                        if r.status_code==200:
                            ready=True
                            break
                    except (httpx.HTTPError,OSError):pass
                    time.sleep(.3)
                if not ready:
                    raise RuntimeError("NO_GO bounded 42s server readiness expired")
                init_idle,_=_wait_idle(client,5)
                streamed=_stream_first(client)
                if streamed["received_stream_events_before_abort"]<2:
                    raise RuntimeError("NO_GO actual model did not stream enough tokens")
                idle_s,post_slots=_wait_idle(client,15)
                fenced_real=_fenced_real_model_stream()
                fenced_idle_s,_=_wait_idle(client,15)
                # The server MUST accept a new bounded generation promptly,
                # rather than leaving the old stream monopolizing the slot.
                followup=_request(client,"/completion",method="POST",
                    json={"prompt":"Complete this phrase briefly: one plus one equals",
                          "stream":False,"n_predict":16,"temperature":0},
                    timeout=12)
                obj=followup.json()
                if not isinstance(obj,dict) or "content" not in obj:
                    raise RuntimeError("NO_GO follow-up inference invalid response")
                last_idle,_=_wait_idle(client,5)
                return {
                    "decision":"PASS_LOOPBACK_REAL_1B_STREAM_ABORT_SLOT_RECOVERY_NOT_FLEET_GO",
                    "host":HOST,"model_architecture_hint":"K2-Horizon-1B-BF16 GGUF",
                    "runtime":"llama-server",
                    "runtime_binary_sha256":__import__("hashlib").sha256(BINARY.read_bytes()).hexdigest(),
                    "model_sha256_not_computed":True,
                    "model_bytes":MODEL.stat().st_size,
                    "loopback_port":PORT,"ports_public":0,
                    "dedicated_canary_process":True,"gpu_layers":0,
                    "threads":2,"slots":1,"context":2048,
                    "x1_preflight":resources,
                    "initial_slot_idle_s":init_idle,
                    **streamed,
                    "server_slot_idle_after_cancel_s":idle_s,
                    "server_slot_idle_after_synthetic_epoch_revoke_s":fenced_idle_s,
                    **fenced_real,
                    "slot_idle_after_followup_s":last_idle,
                    "followup_inference_responded":True,
                    "no_llm_user_data":True,
                    "real_worker_stop_proof":False,
                    "server_slot_released_after_client_abort":True,
                    "separate_authoritative_epoch_proven":False,
                    "elapsed_s":round(time.monotonic()-started,3),
                    "process_terminated_on_exit":True
                }
        finally:
            if server.poll() is None:
                os.killpg(server.pid,signal.SIGTERM)
                try:server.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid,signal.SIGKILL)
                    server.wait(timeout=5)
            # A pre-existing service is NEVER signaled. Only server PID we
            # launched in its own session is cleaned up.
            if server.poll() is None:
                raise RuntimeError("CRITICAL isolated llama canary cleanup failed")


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--run",action="store_true")
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    if not args.run:
        print("PLAN_ONLY: isolated llama canary requires explicit --run")
        return
    result=run()
    rendered=json.dumps(result,sort_keys=True,indent=2)+"\n"
    if args.output:
        path=args.output
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise RuntimeError("NO_GO evidence must be a new absolute private path")
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as out:
            out.write(rendered)
    print(rendered,end="")


if __name__=="__main__":
    main()
