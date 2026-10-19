#!/usr/bin/env python3
"""Lenovo physical llama.cpp slot recovery canary via stdin-only SSH.

Executed with: ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes lenovo 'python3 -' < this_file

No files/services/network settings are installed on Lenovo. An isolated CPU
model process is started with a private temporary log and always terminated.
It is deliberately not an authenticated owner/fencing broker nor a GLM test.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import tempfile
import time

import requests

HOST="scott-Lenovo-ideapad-330S-15IKB"
PORT=12742
LIVE_PORT=1234
BINARY=Path("/home/scott/fleet-data/bin/llama-server")
MODEL=Path("/home/scott/.lmstudio/models/lmstudio-community/LFM2.5-1.2B-Instruct-GGUF/LFM2.5-1.2B-Instruct-Q8_0.gguf")


def memory_safety() -> dict:
    mem={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("MemAvailable:","SwapFree:")):
            k,v,*_=line.split()
            mem[k.rstrip(":")]=int(v)
    pressure=Path("/proc/pressure/memory").read_text().splitlines()[1]
    psi=float(next(x.split("=")[1] for x in pressure.split() if x.startswith("avg10=")))
    return {"available_gib":round(mem["MemAvailable"]/1048576,3),
            "swap_free_gib":round(mem["SwapFree"]/1048576,3),
            "memory_full_avg10":psi}


def preflight() -> tuple[dict,int,str]:
    if socket.gethostname()!=HOST:
        raise RuntimeError("NO_GO not approved Lenovo test host")
    if not BINARY.is_file() or not os.access(BINARY,os.X_OK) or not MODEL.is_file():
        raise RuntimeError("NO_GO model or server unavailable")
    if not 500_000_000 < MODEL.stat().st_size < 2_000_000_000:
        raise RuntimeError("NO_GO GGUF size unexpected")
    metrics=memory_safety()
    if metrics["available_gib"]<5.5 or metrics["memory_full_avg10"]>0.05:
        raise RuntimeError("NO_GO Lenovo memory headroom / PSI elevated")
    with socket.socket() as sock:
        sock.settimeout(.3)
        if sock.connect_ex(("127.0.0.1",PORT))==0:
            raise RuntimeError("NO_GO dedicated loopback canary port already occupied")
    p=subprocess.run(["systemctl","--user","show","fleet-cpu.service",
                      "-p","MainPID","-p","ActiveState","--no-pager"],
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                     timeout=5,check=True)
    unit=dict(part.split("=",1) for part in p.stdout.splitlines() if "=" in part)
    live_pid=int(unit.get("MainPID","0"))
    if unit.get("ActiveState")!="active" or live_pid<=0:
        raise RuntimeError("NO_GO live fleet-cpu service not stable")
    ready=requests.get(f"http://127.0.0.1:{LIVE_PORT}/health",timeout=3)
    if ready.status_code!=200:
        raise RuntimeError("NO_GO live CPU service health not 200")
    return metrics,live_pid,Path(f"/proc/{live_pid}/stat").read_text().split()[21]


def limit_child() -> None:
    resource.setrlimit(resource.RLIMIT_AS,(6*1024**3,6*1024**3))
    os.nice(14)


def slots() -> list[dict]:
    r=requests.get(f"http://127.0.0.1:{PORT}/slots",timeout=3)
    r.raise_for_status()
    data=r.json()
    if not isinstance(data,list) or not data:
        raise RuntimeError("NO_GO serving slot metrics missing")
    return data


def wait_idle(deadline_s: float=15) -> float:
    began=time.monotonic()
    while time.monotonic()-began<deadline_s:
        if not any(bool(s.get("is_processing")) for s in slots()):
            return round(time.monotonic()-began,3)
        time.sleep(.15)
    raise RuntimeError("NO_GO Lenovo canary slot did not recover after stream close")


def observed_stream() -> dict:
    began=time.monotonic()
    payload={"prompt":"Continue this comma-separated sequence with numbers only: 1, 2, 3,",
             "stream":True,"n_predict":512,"temperature":0,"cache_prompt":False}
    response=requests.post(f"http://127.0.0.1:{PORT}/completion",json=payload,
                           stream=True,timeout=(3,18))
    first=None
    events=0
    bytes_seen=0
    try:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if not line or not line.startswith(b"data: "):
                continue
            event=json.loads(line[6:])
            if not isinstance(event,dict) or "content" not in event:
                continue
            if first is None:
                first=round(time.monotonic()-began,3)
            events+=1
            bytes_seen+=len(str(event["content"]).encode())
            if events>=3:
                break
    finally:
        response.close()
    if events!=3:
        raise RuntimeError("NO_GO real Lenovo CPU model produced fewer than 3 SSE events")
    return {"stream_events_before_http_abort":events,
            "synthetic_content_bytes_observed":bytes_seen,
            "time_to_first_event_s":first,
            "http_connection_closed":True}


def execute()->dict:
    mem,live_pid,live_start=preflight()
    began=time.monotonic()
    args=[str(BINARY),"--model",str(MODEL),"--host","127.0.0.1",
          "--port",str(PORT),"--gpu-layers","0","--threads","2",
          "--threads-batch","2","--parallel","1","--ctx-size","1024",
          "--no-webui","--slots","--timeout","20",
          "--alias","research-lenovo-lfm-abort-only"]
    with tempfile.TemporaryDirectory(prefix="lenovo-llama-abort-") as td:
        tmp=Path(td)
        os.chmod(tmp,0o700)
        logpath=tmp/"private-server.log"
        env={**os.environ,"LD_LIBRARY_PATH":str(BINARY.parent),"OMP_NUM_THREADS":"2"}
        with logpath.open("w") as logfile:
            child=subprocess.Popen(args,stdout=logfile,stderr=subprocess.STDOUT,
                                   env=env,start_new_session=True,preexec_fn=limit_child)
        try:
            for _ in range(110):
                if child.poll() is not None:
                    diagnostic=logpath.read_text(errors="replace")[-450:]
                    raise RuntimeError("NO_GO isolated Lenovo server exited: "+diagnostic)
                try:
                    if requests.get(f"http://127.0.0.1:{PORT}/health",timeout=1.2).status_code==200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError("NO_GO isolated Lenovo startup timeout")
            _=wait_idle(3)
            stream=observed_stream()
            recovered=wait_idle(15)
            followup=requests.post(f"http://127.0.0.1:{PORT}/completion",
                json={"prompt":"Finish briefly: 2 + 2 =",
                      "stream":False,"n_predict":12,"temperature":0},
                timeout=15)
            followup.raise_for_status()
            if not isinstance(followup.json(),dict) or "content" not in followup.json():
                raise RuntimeError("NO_GO isolated Lenovo followup response invalid")
            _=wait_idle(4)
            after=requests.get(f"http://127.0.0.1:{LIVE_PORT}/health",timeout=3)
            if after.status_code!=200:
                raise RuntimeError("NO_GO existing Lenovo fleet-cpu lost health")
            actual_start=Path(f"/proc/{live_pid}/stat").read_text().split()[21]
            if actual_start!=live_start:
                raise RuntimeError("NO_GO existing Lenovo service PID identity changed")
            return {
                "decision":"PASS_ISOLATED_LENOVO_REAL_CPU_STREAM_CLOSE_NOT_EPOCH_GO",
                "physical_host":"lenovo","runtime":"llama-server",
                "model_hint":"LFM2.5-1.2B-Instruct-Q8_0",
                "model_size_bytes":MODEL.stat().st_size,
                "model_content_hash_not_measured":True,
                "canary_port":"127.0.0.1:12742",
                "production_listener_touched":False,
                "production_service_pid_starttime_unchanged":True,
                "production_service_health_200_after":True,
                "gpu_layers":0,"cpu_threads":2,"slots":1,
                "memory_limit_gib":6,"initial_pressure":mem,
                **stream,
                "slot_idle_after_abort_s":recovered,
                "followup_inference_success":True,
                "trusted_fencing_epoch_proven":False,
                "llm_process_stop_internal_proven":False,
                "elapsed_s":round(time.monotonic()-began,3),
                "canary_terminated_on_exit":True
            }
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=4)
            if child.poll() is None:
                raise RuntimeError("CRITICAL Lenovo canary process cleanup failed")


def main():
    # Only stdin-based SSH, no --run from public web or unverified env.
    print(json.dumps(execute(),sort_keys=True),flush=True)


if __name__=="__main__":
    main()
