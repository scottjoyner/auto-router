#!/usr/bin/env python3
"""x1 Auto-Router wrapper -> real isolated Lenovo 1.2B HTTP generation.

The temporary Lenovo service only binds its own loopback. A strict-hostkey
temporary SSH local forward from x1 loopback transports synthetic traffic.
No production service/credential/ACL/model content/host file is modified.

The authority in this experiment is an in-process synthetic verifier,
NOT actual Task owner, signed PostgreSQL epoch or distributed fence.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import socket
import subprocess
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parent
HOST="x1-370"
PORT=12743
SSH=["ssh","-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
     "-o","ConnectTimeout=5","-o","ServerAliveInterval=5",
     "-o","ServerAliveCountMax=2"]


def preflight():
    if socket.gethostname()!=HOST:
        raise RuntimeError("NO_GO x1 controller only")
    if not (ROOT/"lenovo_loopback_abort.py").is_file() or not (
        ROOT/"lenovo_remote_canary_host.py"
    ).is_file():
        raise RuntimeError("NO_GO reviewed remote canary source missing")
    sock=socket.socket()
    try:
        if sock.connect_ex(("127.0.0.1",PORT))==0:
            raise RuntimeError("NO_GO local tunnel port already occupied")
    finally:sock.close()
    probe=subprocess.run(SSH+["lenovo","hostname"],capture_output=True,
                         text=True,timeout=9,check=True)
    if probe.stdout.strip()!="scott-Lenovo-ideapad-330S-15IKB":
        raise RuntimeError("NO_GO SSH peer physical identity unexpected")


def read_json_line(proc:subprocess.Popen,timeout_s:float)->dict:
    if proc.stdout is None:
        raise RuntimeError("NO_GO remote stdout absent")
    fds,_,_=select.select([proc.stdout],[],[],timeout_s)
    if not fds:
        raise RuntimeError("NO_GO remote event deadline")
    line=proc.stdout.readline()
    if not line:
        stderr=proc.stderr.read(400) if proc.stderr and proc.poll() is not None else ""
        raise RuntimeError("NO_GO remote canary ended before event "+stderr)
    data=json.loads(line)
    if not isinstance(data,dict):
        raise RuntimeError("NO_GO non-object remote event")
    return data


def temporary_remote_command()->str:
    support=base64.b64encode((ROOT/"lenovo_loopback_abort.py").read_bytes()).decode()
    server=base64.b64encode((ROOT/"lenovo_remote_canary_host.py").read_bytes()).decode()
    program=(
        "import base64\n"
        "ns={'__name__':'lenovo_safe_canary_support'}\n"
        f"exec(compile(base64.b64decode('{support}'),'<lenovo_safe_support>','exec'),ns)\n"
        f"exec(compile(base64.b64decode('{server}'),'<lenovo_remote_server>','exec'),"
        "{'__name__':'__main__','SUPPORT':ns})"
    )
    return "python3 -u -c "+shlex.quote(program)


def wait_tunnel(port:int,timeout:float=5):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}",
                              timeout=1.4,trust_env=False) as client:
                if client.get("/health").status_code==200:
                    return
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    raise RuntimeError("NO_GO x1 Lenovo reverse gateway not ready")


def slot_idle(timeout:float=10)->float:
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}",
                          timeout=2,trust_env=False) as client:
            slots=client.get("/slots").json()
        if isinstance(slots,list) and slots and not any(
            bool(item.get("is_processing")) for item in slots
        ):
            return round(time.monotonic()-started,3)
        time.sleep(.15)
    raise RuntimeError("NO_GO real Lenovo CPU canary remained busy after router stream abort")


async def run_wrapped_provider()->dict:
    root=ROOT.parents[1]
    sys.path.insert(0,str(root/"src"))
    sys.path.insert(0,str(root/"tests"))
    from auto_router.models import ProviderConfig, RouterRequest
    from auto_router.providers import OpenAICompatibleProvider
    from auto_router.stream_effect_fence import StreamEffectDenied
    from test_stream_lifecycle import _module,_request,_candidate

    class SimulatedRevocation:
        def __init__(self):
            self.calls=[]
            self.aborts=0
            self.preflights=0
            self.runtime_busy_at_revoke=False
        async def preflight(self,*,request,candidate):
            self.preflights+=1
            return True
        async def accept_chunk(self,*,request,candidate,sequence,chunk_sha256):
            if type(chunk_sha256) is not str or len(chunk_sha256)!=64:
                raise AssertionError("NO_GO invalid signed chunk digest")
            self.calls.append(sequence)
            if sequence==4:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{PORT}",timeout=2,
                    trust_env=False
                ) as client:
                    response=await client.get("/slots")
                    response.raise_for_status()
                    data=response.json()
                    self.runtime_busy_at_revoke=any(
                        bool(slot.get("is_processing")) for slot in data
                    )
            return sequence<4
        async def abort(self,*,request):
            self.aborts+=1
            return True

    cfg=ProviderConfig(
        id="isolated-lenovo-lfm-canary",
        name="isolated-lenovo-lfm-canary",
        type="openai",
        base_url=f"http://127.0.0.1:{PORT}/v1",
        models=[],
    )
    provider=OpenAICompatibleProvider(cfg,timeout_seconds=20)
    actual_request=RouterRequest(
        request_id="synthetic-lenovo-epoch-revoke-only",
        route="completions",
        model="research-lenovo-lfm-abort-only",
        stream=True,
        raw_body={
            "model":"research-lenovo-lfm-abort-only",
            "prompt":"Continue sequential positive numbers separated by commas, starting at 1: 1, 2, 3,",
            "stream":True,
            "max_tokens":256,
            "temperature":0,
        },
    )
    async def live_model_body():
        original=await provider.stream_completions(
            actual_request,"research-lenovo-lfm-abort-only"
        )
        try:
            async for chunk in original.body:
                yield chunk
        finally:
            await original.body.aclose()

    module,_,_,policy,circuits,quota,idempotency,outbox=_module(live_model_body)
    authority=SimulatedRevocation()
    module.state.stream_effect_authority=authority
    req=_request()
    req.route="completions"
    req.request_id="synthetic-lenovo-epoch-revoke-only"

    before=(os.environ.get("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE"),
            os.environ.get("AUTO_ROUTER_REQUIRE_OWNER_BINDING"))
    os.environ["AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE"]="true"
    os.environ["AUTO_ROUTER_REQUIRE_OWNER_BINDING"]="true"
    try:
        response=await module._dispatch_stream(None,_candidate(),req)
        released=[]
        denied=False
        try:
            async for data in response.body:
                released.append(len(data))
        except StreamEffectDenied:
            denied=True
        if not denied or len(released)!=3:
            raise RuntimeError("NO_GO real Lenovo router output was not fenced at chunk 4")
    finally:
        for key,old in zip(("AUTO_ROUTER_REQUIRE_STREAM_EFFECT_FENCE",
                            "AUTO_ROUTER_REQUIRE_OWNER_BINDING"),before):
            if old is None:os.environ.pop(key,None)
            else:os.environ[key]=old
    if authority.calls!=[1,2,3,4] or authority.preflights!=1 or authority.aborts!=1:
        raise RuntimeError("NO_GO real Lenovo stream authority lifecycle mismatch")
    if authority.runtime_busy_at_revoke is not True:
        raise RuntimeError("NO_GO real Lenovo runtime was idle before simulated revocation")
    if not outbox.events or outbox.events[-1].event_type!="router.stream.failed":
        raise RuntimeError("NO_GO revoked Lenovo stream was not marked failed")
    if outbox.events[-1].payload.get("acceptance_state")!="possibly_accepted":
        raise RuntimeError("NO_GO revoked Lenovo completion misclassified")
    return {
        "provider":"OpenAICompatibleProvider.stream_completions",
        "adapter":"stream_lifecycle._dispatch_stream",
        "real_lenovo_model_busy_when_chunk_four_denied":True,
        "authorized_output_chunks":len(released),
        "suppressed_real_output_chunk_four":True,
        "synthetic_preflight_calls":authority.preflights,
        "synthetic_chunk_check_sequences":authority.calls,
        "synthetic_abort_calls":authority.aborts,
        "terminal_state":"possibly_accepted",
        "real_remote_epoch_authority":False
    }


def run()->dict:
    preflight()
    began=time.monotonic()
    remote=None
    tunnel=None
    cleanup=None
    ready_confirmed=False
    try:
        remote=subprocess.Popen(
            SSH+["lenovo",temporary_remote_command()],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,bufsize=1
        )
        ready=read_json_line(remote,45)
        if ready.get("event")!="ready" or ready.get("port")!=12742:
            raise RuntimeError("NO_GO Lenovo remote canary did not pass startup")
        ready_confirmed=True
        tunnel=subprocess.Popen(
            SSH+["-N","-o","ExitOnForwardFailure=yes",
                 "-L",f"127.0.0.1:{PORT}:127.0.0.1:12742","lenovo"],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE
        )
        wait_tunnel(PORT)
        measured=asyncio.run(run_wrapped_provider())
        idle_after_abort=slot_idle()
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}",
                          timeout=20,trust_env=False) as client:
            result=client.post("/completion",json={
                "prompt":"Finish very briefly: 3 plus 3 =",
                "stream":False,"n_predict":12,"temperature":0
            })
            result.raise_for_status()
            if not isinstance(result.json(),dict) or "content" not in result.json():
                raise RuntimeError("NO_GO followup Lenovo loopback generation invalid")
        idle_after_followup=slot_idle(5)
        return {
            "decision":"PASS_PHYSICAL_X1_ROUTER_LENOVO_REAL_MODEL_STREAM_ABORT_NOT_PRODUCTION",
            "router_host":"x1-370","model_host":"lenovo",
            "model_hint":"LFM2.5-1.2B-Instruct-Q8_0",
            "isolation":"temporary loopback-only Lenovo process and x1 SSH forward",
            "x1_tunnel":"127.0.0.1:12743",
            "lenovo_canary":"127.0.0.1:12742",
            "port_public":0,"gpu_layers":0,"cpu_threads":2,
            **measured,
            "lenovo_slot_idle_after_abort_s":idle_after_abort,
            "lenovo_slot_idle_after_followup_s":idle_after_followup,
            "followup_real_model_generation_success":True,
            "production_route_untouched":True,
            "independently_authenticated_human_principals":False,
            "durable_epoch_broker_involved":False,
            "model_process_internal_abort_proven":False,
            "elapsed_s":round(time.monotonic()-began,3),
            "temporary_resources_cleaned":True,
        }
    finally:
        if tunnel is not None:
            if tunnel.poll() is None:
                tunnel.terminate()
                try:tunnel.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=4)
            if tunnel.stderr:tunnel.stderr.close()
        if remote is not None:
            if remote.poll() is None and remote.stdin:
                try:
                    remote.stdin.write("STOP\n")
                    remote.stdin.flush()
                    cleanup=read_json_line(remote,12)
                except (BrokenPipeError,RuntimeError,OSError):
                    pass
            if remote.stdin:remote.stdin.close()
            try:remote.wait(timeout=8)
            except subprocess.TimeoutExpired:
                remote.kill()
                remote.wait(timeout=5)
            if remote.stderr:remote.stderr.close()
            if remote.stdout:remote.stdout.close()
            if ready_confirmed and (
                cleanup is None
                or cleanup.get("event")!="stopped"
                or cleanup.get("isolated_process_terminated") is not True
                or cleanup.get("live_health_200") is not True
                or cleanup.get("live_process_identity_unchanged") is not True
                or remote.returncode != 0
            ):
                raise RuntimeError("CRITICAL Lenovo production service/canary cleanup not verified")


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--run",action="store_true")
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    if not args.run:
        print("PLAN_ONLY: no model, tunnel or cross-host stream started")
        return
    result=run()
    text=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if args.output:
        p=args.output
        if not p.is_absolute() or p.exists() or p.is_symlink():
            raise RuntimeError("NO_GO evidence target must be new absolute path")
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as f:f.write(text)
    print(text,end="")


if __name__=="__main__":
    main()
