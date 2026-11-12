"""Synthetic proof that Router cutover preserves mounted state and access."""
from __future__ import annotations

import json

import pytest

from scripts import inspect_router_cutover_topology as p

IMAGE="sha256:"+"a"*64

def setup():
    compose={"services":{"llm-router":{
        "image":"git-router",
        "ports":["8088:8088"],
        "networks":["default","assistx"],
    }}}
    instance={
        "Name":"/git-router-1",
        "Image":IMAGE,
        "Config":{"Labels":{"com.docker.compose.project":"assistx-fleet",
                            "com.docker.compose.service":"llm-router"}},
        "HostConfig":{"PortBindings":{"8088/tcp":[{"HostIp":"","HostPort":"8088"}]}},
        "NetworkSettings":{"Networks":{"assistx-fleet_default":{},"git_default":{}}},
        "Mounts":[{"Destination":path,"RW":mode}
                  for path,mode in p.REQUIRED_MOUNTS.items()],
    }
    return compose,instance

def test_read_only_topology_matches_expected_contract():
    compose,instance=setup()
    result=p.evaluate(compose,instance,True)
    assert result["compose_compatibility_pass"] is True
    assert result["all_required_mounts_preserved"] is True
    assert result["running_rollback_image_present"] is True
    assert result["production_cutover_allowed"] is False
    assert result["running_rollback_image_restored_and_tested"] is False
    assert result["target_candidate_image_promoted"] is False

@pytest.mark.parametrize("target,mode",[
    ("/data",False),("/artifacts",False),("/app/config",True),
    ("/run/secrets",True),
])
def test_read_write_changes_fail_compatibility(target,mode):
    compose,instance=setup()
    for m in instance["Mounts"]:
        if m["Destination"]==target:m["RW"]=mode
    result=p.evaluate(compose,instance,True)
    assert result["mount_read_write_contract"][target] is False
    assert result["compose_compatibility_pass"] is False
    assert result["production_cutover_allowed"] is False

def test_missing_required_network_denied():
    compose,instance=setup()
    del instance["NetworkSettings"]["Networks"]["git_default"]
    result=p.evaluate(compose,instance,True)
    assert result["live_networks_match"] is False
    assert result["compose_compatibility_pass"] is False

def test_altered_production_compose_port_denied():
    compose,instance=setup()
    compose["services"]["llm-router"]["ports"]=["127.0.0.1:8088:8088"]
    result=p.evaluate(compose,instance,True)
    assert result["compose_expected_ports"] is False
    assert result["compose_compatibility_pass"] is False

def test_missing_rollback_image_cannot_assert_restored():
    compose,instance=setup()
    result=p.evaluate(compose,instance,False)
    assert result["running_rollback_image_present"] is False
    assert result["running_rollback_image_restored_and_tested"] is False
    assert result["production_cutover_allowed"] is False

def test_no_sensitive_mount_sources_or_env_appear_in_report():
    compose,instance=setup()
    instance["Config"]["Env"]=["AUTO_ROUTER_ADMIN_TOKEN=TOPSECRET"]
    instance["Mounts"][0]["Source"]="/home/private/secret-host-file"
    serialized=json.dumps(p.evaluate(compose,instance,True))
    assert "TOPSECRET" not in serialized
    assert "secret-host-file" not in serialized

def test_unexpected_service_raises():
    with pytest.raises(p.TopologyDenied):
        p.evaluate({"services":{}},setup()[1],True)
