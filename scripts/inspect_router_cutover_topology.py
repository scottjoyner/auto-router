#!/usr/bin/env python3
"""Read-only Router deployment compatibility and rollback-source inventory.

No admin credentials or response bodies, no file writes, no Docker mutations,
and no source/mount host paths are printed. A present rollback image does not
by itself prove it can be restored safely. No promotion is authorized here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import yaml

REQUIRED_MOUNTS = {
    "/app/config": False,
    "/data": True,
    "/artifacts": True,
    "/run/secrets": False,
    "/run/assistx-previous-internal-token": False,
    "/run/assistx_runtime_projection_public_key.pem": False,
}
REQUIRED_NETWORKS = {"assistx-fleet_default", "git_default"}
REQUIRED_PORTS = {"8088/tcp": "8088"}

class TopologyDenied(RuntimeError):
    pass

def docker_json(*args: str):
    result = subprocess.run(["docker",*args],capture_output=True,text=True,
                            check=True,timeout=8)
    parsed = json.loads(result.stdout)
    if not isinstance(parsed,list) or len(parsed)!=1:
        raise TopologyDenied("invalid Docker inspect response")
    return parsed[0]

def evaluate(compose: dict, container: dict, image_present: bool) -> dict:
    service = (compose.get("services") or {}).get("llm-router")
    if not isinstance(service,dict):
        raise TopologyDenied("missing llm-router service")
    labels=(container.get("Config") or {}).get("Labels") or {}
    settings=container.get("HostConfig") or {}
    mount_actual = {m.get("Destination"):m.get("RW")
                    for m in container.get("Mounts",[])}
    actual_net=set((container.get("NetworkSettings") or {}).get("Networks") or {})
    bindings=(settings.get("PortBindings") or {}).get("8088/tcp") or []
    external_port=any(str(b.get("HostPort"))=="8088" for b in bindings)
    expected_compose_ports=service.get("ports") == ["8088:8088"]
    expected_compose_networks=set(service.get("networks") or []) == {"default","assistx"}
    image_spec=service.get("image")=="git-router" and "build" not in service
    mounted={name:mount_actual.get(name) is mode for name,mode in REQUIRED_MOUNTS.items()}
    topology={
        "schema":1,
        "running_container":container.get("Name","").lstrip("/"),
        "running_image_sha256":container.get("Image"),
        "compose_owner_matches":labels.get("com.docker.compose.project")=="assistx-fleet" and
                                labels.get("com.docker.compose.service")=="llm-router",
        "compose_uses_existing_image_tag":image_spec,
        "compose_expected_ports":expected_compose_ports,
        "compose_expected_networks":expected_compose_networks,
        "live_port_8088_published":external_port,
        "live_networks_match":actual_net==REQUIRED_NETWORKS,
        "mount_read_write_contract":mounted,
        "all_required_mounts_preserved":all(mounted.values()),
        "running_rollback_image_present":bool(image_present),
        "running_rollback_image_restored_and_tested":False,
        "target_candidate_image_present":False,
        "target_candidate_image_tested_on_prod_mounts":False,
        "target_candidate_image_promoted":False,
        "production_cutover_allowed":False,
    }
    # Report all deviations but never print mount source host paths or env.
    topology["compose_compatibility_pass"]=all((
        topology["compose_owner_matches"], image_spec,
        expected_compose_ports,expected_compose_networks,
        external_port,topology["live_networks_match"],
        topology["all_required_mounts_preserved"],
    ))
    return topology

def inspect(compose_path: Path) -> dict:
    if compose_path.is_symlink() or not compose_path.is_file():
        raise TopologyDenied("untrusted or unavailable compose file")
    doc=yaml.safe_load(compose_path.read_text())
    if not isinstance(doc,dict):
        raise TopologyDenied("invalid compose format")
    container=docker_json("inspect","git-router-1")
    image=container.get("Image","")
    if not isinstance(image,str) or len(image)!=71 or not image.startswith("sha256:"):
        raise TopologyDenied("missing immutable running image ID")
    try:
        actual=docker_json("image","inspect",image)
        present=actual.get("Id")==image
    except subprocess.CalledProcessError:
        present=False
    result=evaluate(doc,container,present)
    result["observed_utc"]=datetime.now(timezone.utc).isoformat()
    result["read_only"]=True
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--compose-file",type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(inspect(args.compose_file),sort_keys=True,indent=2))
