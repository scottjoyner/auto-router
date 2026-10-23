#!/usr/bin/env python3
"""Read-only, no-secret Router admin deployment drift and rollback inventory.

This is NOT a release gate waiver or a runtime canary; it never builds, starts,
restarts, removes, connects to external hosts, or reads admin response bodies.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ADMIN = {"/admin/providers", "/admin/load", "/admin/circuits/reset",
                  "/admin/circuits/reset/{owner:path}"}

def run_cmd(*args):
    result = subprocess.run(args,capture_output=True,text=True,check=True,timeout=8)
    return result.stdout.strip()

def guarded_admin_routes(source):
    module = ast.parse(source)
    routes = {}
    for fn in module.body:
        if not isinstance(fn,(ast.AsyncFunctionDef,ast.FunctionDef)):
            continue
        for decorator in fn.decorator_list:
            if not isinstance(decorator,ast.Call) or not decorator.args:
                continue
            arg=decorator.args[0]
            if not isinstance(arg,ast.Constant) or not isinstance(arg.value,str):
                continue
            if not arg.value.startswith("/admin/"):
                continue
            if arg.value in routes:
                raise ValueError("duplicate admin route")
            gate=any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                     and n.func.id=="Depends" and n.args
                     and isinstance(n.args[0],ast.Name)
                     and n.args[0].id=="require_admin" for n in ast.walk(fn.args))
            routes[arg.value]=gate
    return routes

def status_only(path):
    url="http://127.0.0.1:8088"+path
    req=urllib.request.Request(url,method="GET",headers={"Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError,TimeoutError):
        return "unreachable"

def inventory():
    source_path=ROOT/"src/auto_router/main.py"
    main_head=run_cmd("git","-C",str(ROOT),"rev-parse","HEAD")
    source=run_cmd("git","-C",str(ROOT),"show",
                   main_head+":src/auto_router/main.py")
    if source_path.read_text()!=source+"\n" and source_path.read_text()!=source:
        raise RuntimeError("checked-out source differs from exact commit")
    routes=guarded_admin_routes(source)
    if not EXPECTED_ADMIN<=set(routes):
        raise RuntimeError("expected source admin routes missing")
    container=json.loads(run_cmd("docker","inspect","git-router-1"))[0]
    image=container["Image"]
    image_record=json.loads(run_cmd("docker","image","inspect",image))[0]
    env=container["Config"].get("Env",[])
    configured=any(e.startswith("AUTO_ROUTER_ADMIN_TOKEN=")
                   and e.partition("=")[2] for e in env)
    mounts=[m.get("Destination") for m in container.get("Mounts",[])
            if m.get("Destination")]
    mem={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("SwapTotal:","SwapFree:","MemAvailable:")):
            key,value=line.split(":",1)
            mem[key]=int(value.strip().split()[0])*1024
    statuses={path:status_only(path) for path in
              ("/admin/providers","/admin/circuits","/admin/admission")}
    source_ok=all(routes.values())
    anon_exposure=(statuses["/admin/providers"]==200 or
                   statuses["/admin/circuits"]==200)
    swap_full=mem.get("SwapTotal",0)>0 and (
        mem.get("SwapFree",0)*100<mem.get("SwapTotal",0)*5)
    result={
        "schema":1,
        "observed_utc":datetime.now(timezone.utc).isoformat(),
        "candidate_commit":main_head,
        "candidate_admin_routes":len(routes),
        "candidate_admin_routes_guarded":sum(routes.values()),
        "candidate_admin_source_guarded":source_ok,
        "running_image_sha256":image,
        "running_image_created_at":image_record.get("Created"),
        "running_container_status":container["State"].get("Status"),
        "running_admin_token_configured_nonempty":bool(configured),
        "running_mount_targets_count":len(mounts),
        "anonymous_admin_http_status":statuses,
        "anonymous_admin_exposed":anon_exposure,
        "available_memory_bytes":mem.get("MemAvailable"),
        "swap_total_bytes":mem.get("SwapTotal"),
        "swap_free_bytes":mem.get("SwapFree"),
        "swap_near_full":swap_full,
        "running_image_exact_head_verified":False,
        "candidate_image_canary_passed":False,
        "authorized_operator_canary_passed":False,
        "rollback_restore_verified":False,
        "production_cutover_allowed":False,
        "notes":"Read-only evidence only; no secrets, HTTP response bodies, or mutation.",
    }
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    print(json.dumps(inventory(),sort_keys=True,indent=2))
