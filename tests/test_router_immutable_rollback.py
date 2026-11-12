"""Hermetic negative tests for immutable Router rollback metadata preflight."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import inspect_router_immutable_rollback as r
from scripts import inspect_router_cutover_topology as topo

OLD="sha256:"+"a"*64
NEW="sha256:"+"b"*64
SECRET="NEVER_DISPLAY_THIS_TOKEN"


def fixture():
    cfg={"services":{"llm-router":{"image":"git-router","ports":["8088:8088"],"networks":["default","assistx"]}}}
    live={"Name":"/git-router-1","Image":OLD,"State":{"Status":"running"},
          "Config":{"Env":["AUTO_ROUTER_ADMIN_TOKEN="+SECRET],
                    "Labels":{"com.docker.compose.project":"assistx-fleet",
                              "com.docker.compose.service":"llm-router",
                              "com.docker.compose.project.config_files":"/tmp/expected.yml"}},
          "HostConfig":{"RestartPolicy":{"Name":"unless-stopped"},
                        "PortBindings":{"8088/tcp":[{"HostPort":"8088","HostIp":"0.0.0.0"}]}},
          "NetworkSettings":{"Networks":{"git_default":{},"assistx-fleet_default":{}}},
          "Mounts":[{"Destination":p,"RW":rw,"Source":"/SECRET/"+SECRET}
                    for p,rw in topo.REQUIRED_MOUNTS.items()]}
    return cfg,live


def evidence(cfg=None,live=None,previous=None,tag=None,candidate=None):
    c,l=fixture()
    return r.evaluate(cfg if cfg is not None else c,live if live is not None else l,
                      previous if previous is not None else {"Id":OLD},
                      tag if tag is not None else {"Id":OLD},
                      candidate if candidate is not None else {"Id":NEW},OLD,NEW)


def test_everything_present_still_hold():
    report=evidence()
    assert report["verdict"]=="ROLLBACK_METADATA_PRECHECK_HOLD"
    assert report["blocker_codes"]==[]
    assert report["previous_image_cached_by_immutable_id"]
    assert report["candidate_image_cached_by_immutable_id"]
    for k in ("production_cutover_allowed","production_rollback_proven",
              "tag_is_immutable_rollback_reference",
              "previous_image_export_hash_independently_verified",
              "restoration_on_disposable_node_rehearsed"):
        assert report[k] is False
    assert SECRET not in json.dumps(report)


@pytest.mark.parametrize("what,reason",[
    ("old_missing","PREVIOUS_IMAGE_NOT_CACHED"),
    ("candidate_missing","CANDIDATE_IMAGE_NOT_CACHED_ON_THIS_HOST"),
    ("tag_drift","MUTABLE_COMPOSE_TAG_DRIFT"),
    ("running_image_changed","RUNNING_IMAGE_CHANGED"),
    ("stopped","ROUTER_NOT_RUNNING"),
    ("restart_changed","RESTART_POLICY_DRIFT"),
    ("mount_changed","COMPOSE_OR_LIVE_TOPOLOGY_DRIFT"),
    ("network_changed","COMPOSE_OR_LIVE_TOPOLOGY_DRIFT"),
    ("compose_changed","COMPOSE_OR_LIVE_TOPOLOGY_DRIFT"),
])
def test_rollback_drifts_never_promote(what,reason):
    cfg,live=fixture()
    prev={"Id":OLD}
    cand={"Id":NEW}
    tag={"Id":OLD}
    if what=="old_missing":prev={"Id":NEW}
    if what=="candidate_missing":cand={"Id":OLD}
    if what=="tag_drift":tag={"Id":NEW}
    if what=="running_image_changed":live["Image"]=NEW
    if what=="stopped":live["State"]["Status"]="exited"
    if what=="restart_changed":live["HostConfig"]["RestartPolicy"]["Name"]="always"
    if what=="mount_changed":live["Mounts"][0]["RW"]=not live["Mounts"][0]["RW"]
    if what=="network_changed":del live["NetworkSettings"]["Networks"]["git_default"]
    if what=="compose_changed":cfg["services"]["llm-router"]["image"]="replacement"
    out=r.evaluate(cfg,live,prev,tag,cand,OLD,NEW)
    assert reason in out["blocker_codes"]
    assert out["production_cutover_allowed"] is False
    assert SECRET not in json.dumps(out)


@pytest.mark.parametrize("args",[
    ("rm","git-router-1"),
    ("image","tag",OLD),
    ("inspect","assistx-api"),
    ("image","inspect","git-router;echo secret"),
])
def test_unapproved_docker_commands_denied_without_exec(monkeypatch,args):
    monkeypatch.setattr(r.subprocess,"run",lambda *a,**kw:(_ for _ in ()).throw(AssertionError()))
    with pytest.raises(r.RollbackPreflightDenied,match="unapproved"):
        r.docker_read(*args)


def test_only_exact_read_and_no_docker_stderr_leaks(monkeypatch):
    seen=[]
    def fake(argv,**kwargs):
        seen.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps([{"Id":OLD}]),stderr=SECRET)
    monkeypatch.setattr(r.subprocess,"run",fake)
    assert r.docker_read("image","inspect",OLD)=={"Id":OLD}
    assert seen[0][0]==["docker","image","inspect",OLD]
    assert seen[0][1]["capture_output"] is True


def test_optional_absence_and_required_error_never_expose_stderr(monkeypatch):
    monkeypatch.setattr(r.subprocess,"run",lambda *a,**kw:
                        SimpleNamespace(returncode=1,stdout="",stderr=SECRET))
    assert r.docker_read("image","inspect",NEW,optional=True) is None
    with pytest.raises(r.RollbackPreflightDenied) as caught:
        r.docker_read("image","inspect",OLD)
    assert SECRET not in str(caught.value)


def test_wrong_image_id_or_same_image_denied():
    cfg,live=fixture()
    with pytest.raises(r.RollbackPreflightDenied):
        r.evaluate(cfg,live,None,None,None,"git-router",NEW)
    with pytest.raises(r.RollbackPreflightDenied):
        r.evaluate(cfg,live,None,None,None,OLD,OLD)


def test_compose_must_match_container_label(tmp_path):
    _,live=fixture()
    real=tmp_path/"compose.yml"
    real.write_text("services:\n  llm-router:\n    image: git-router\n")
    live["Config"]["Labels"]["com.docker.compose.project.config_files"]=str(real)
    assert r.read_compose(real,live)["services"]["llm-router"]["image"]=="git-router"
    fake=tmp_path/"wrong.yml"
    fake.write_text("services: {}")
    with pytest.raises(r.RollbackPreflightDenied,match="does not match"):
        r.read_compose(fake,live)


def test_compose_symlink_and_missing_path_denied_without_path_leak(tmp_path):
    _,live=fixture()
    real=tmp_path/"compose.yml"
    real.write_text("services: {}")
    link=tmp_path/"link.yml"
    link.symlink_to(real)
    live["Config"]["Labels"]["com.docker.compose.project.config_files"]=str(link)
    with pytest.raises(r.RollbackPreflightDenied):
        r.read_compose(link,live)
    missing=tmp_path/"SECRET_PRIVATE_FILE.yml"
    with pytest.raises(r.RollbackPreflightDenied) as err:
        r.read_compose(missing,live)
    assert "SECRET_PRIVATE_FILE" not in str(err.value)


def test_main_never_returns_zero_or_promotion_even_if_all_metadata_complete(monkeypatch,capsys):
    cfg,live=fixture()
    data={("inspect",r.ROUTER):live,("image","inspect",OLD):{"Id":OLD},
          ("image","inspect",NEW):{"Id":NEW},
          ("image","inspect",r.MUTABLE_TAG):{"Id":OLD}}
    monkeypatch.setattr(r,"docker_read",lambda *args,**kw:data.get(args))
    monkeypatch.setattr(r,"read_compose",lambda *args:cfg)
    monkeypatch.setattr(sys,"argv",["audit","--compose-file","/tmp/expected.yml",
                                  "--expected-running-image",OLD,"--expected-candidate-image",NEW])
    assert r.main()==2
    report=json.loads(capsys.readouterr().out)
    assert report["verdict"]=="ROLLBACK_METADATA_PRECHECK_HOLD"
    assert not report["production_cutover_allowed"]


def test_exception_fails_closed_and_sanitizes(monkeypatch,capsys):
    monkeypatch.setattr(r,"docker_read",lambda *a,**kw:
                        (_ for _ in ()).throw(r.RollbackPreflightDenied(SECRET)))
    monkeypatch.setattr(sys,"argv",["audit","--compose-file","/tmp/expected.yml",
                                  "--expected-running-image",OLD,"--expected-candidate-image",NEW])
    assert r.main()==2
    data=capsys.readouterr().out
    assert SECRET not in data
    assert "ROLLBACK_INSPECTION_UNAVAILABLE_HOLD" in data
