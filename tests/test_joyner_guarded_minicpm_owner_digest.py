"""Offline guarded-owner digest custody; no model, network, or secret access."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts import joyner_guarded_minicpm_owner_digest as g


def proc_fixture(tmp_path, model, *, uid=1001, pid=4620, port=18320,
                 extra_flags=None):
    root = tmp_path / "proc"
    root.mkdir(exist_ok=True)
    entry = root / str(pid)
    entry.mkdir()
    (entry / "comm").write_text("llama-server\n")
    args = ["/usr/bin/llama-server", "--port", str(port), "--model",
            str(model), "--api-key-file", "/private/SYNTHETIC-SECRET"]
    if extra_flags:
        args.extend(extra_flags)
    (entry / "cmdline").write_bytes(b"\0".join(x.encode() for x in args)+b"\0")
    (entry / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (entry / "stat").write_text(
        f"{pid} (llama-server) S " + " ".join(["0"]*18+["4242"]+["0"]*10))
    return root


def test_constants_stay_pinned_to_guarded_canary():
    assert g.PINNED_BYTES == 688065920
    assert g.PINNED_SHA256 == "81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa"
    assert g.PORT == 18320


def test_wrong_host_is_total_no_op():
    def forbidden(*a, **kw):
        raise AssertionError("hashed model on unapproved host")
    x = g.audit(hostname="x1-370", euid=1001,
                proc_root=Path("/private"), hasher=forbidden)
    assert x["verdict"] == "HOLD"
    assert not x["observer_host_verified"]
    assert not x["guarded_disk_model_matches_pinned_sha256"]


def test_root_cannot_attest(tmp_path):
    x = g.audit(hostname="joyner", euid=0,
                proc_root=tmp_path / "missing", hasher=lambda *a: pytest.fail("root hashed"))
    assert not x["nonroot_observer_verified"]
    assert not x["guarded_disk_model_matches_pinned_sha256"]


def test_exact_owner_process_and_digest_still_holds(tmp_path):
    model = tmp_path / "guarded.gguf"
    model.write_bytes(b"synthetic")
    proc = proc_fixture(tmp_path, model)
    seen=[]
    def fake_hash(path, home, uid):
        seen.append((path,home,uid))
        return True
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,hasher=fake_hash)
    assert x["exact_guarded_process_owner_and_identity_verified"] is True
    assert x["guarded_disk_model_matches_pinned_sha256"] is True
    assert x["guarded_process_pid"]==4620
    assert seen == [(model,tmp_path,1001)]
    assert x["resident_gpu_weights_verified"] is False
    assert x["file_hash_receipt_is_signed"] is False
    assert x["router_cutover_authorized"] is False
    assert "SYNTHETIC-SECRET" not in json.dumps(x)


def test_wrong_owner_never_hashes(tmp_path):
    model=tmp_path/"model.gguf";model.write_bytes(b"test")
    proc=proc_fixture(tmp_path,model,uid=1002)
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,
              hasher=lambda *a:pytest.fail("wrong owner hashed"))
    assert not x["exact_guarded_process_owner_and_identity_verified"]
    assert not x["guarded_disk_model_matches_pinned_sha256"]


def test_duplicate_guarded_port_process_denied(tmp_path):
    model=tmp_path/"model.gguf";model.write_bytes(b"test")
    proc=proc_fixture(tmp_path,model)
    proc_fixture(tmp_path,model,pid=4621)
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,
              hasher=lambda *a:pytest.fail("duplicate hashed"))
    assert not x["guarded_disk_model_matches_pinned_sha256"]


@pytest.mark.parametrize("port,flags",[(18321,None),(18320,["--model","/other.gguf"]),
                                    (18320,["--port","18320"])])
def test_wrong_or_ambiguous_process_flags_denied(tmp_path,port,flags):
    model=tmp_path/"model.gguf";model.write_bytes(b"test")
    proc=proc_fixture(tmp_path,model,port=port,extra_flags=flags)
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,
              hasher=lambda *a:pytest.fail("ambiguous process hashed"))
    assert not x["guarded_disk_model_matches_pinned_sha256"]


def test_missing_key_file_flag_denied_without_reading_key(tmp_path):
    model=tmp_path/"model.gguf";model.write_bytes(b"test")
    proc=proc_fixture(tmp_path,model)
    proc.joinpath("4620","cmdline").write_bytes(
        b"/usr/bin/llama-server\0--port\018320\0--model\0"+str(model).encode()+b"\0")
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,
              hasher=lambda *a:pytest.fail("key flag missing"))
    assert not x["guarded_disk_model_matches_pinned_sha256"]


def test_process_restart_during_hash_is_hold(tmp_path):
    model=tmp_path/"model.gguf";model.write_bytes(b"test")
    proc=proc_fixture(tmp_path,model)
    def hash_and_rewrite(*args):
        (proc/"4620"/"stat").write_text(
            "4620 (llama-server) S "+" ".join(["0"]*18+["9999"]+["0"]*10))
        return True
    x=g.audit(hostname="joyner",euid=1001,proc_root=proc,home=tmp_path,
              hasher=hash_and_rewrite)
    assert not x["guarded_disk_model_matches_pinned_sha256"]
    assert not x["exact_guarded_process_owner_and_identity_verified"]


def test_real_fd_hash_accepts_synthetic_owned_regular_file(tmp_path):
    path=tmp_path/"model.gguf";data=b"synthetic-immutable-bytes";path.write_bytes(data)
    assert g.hash_file(path,tmp_path,os.geteuid(),expected_bytes=len(data),
                       expected_sha256=hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("change",["sha","length","symlink","missing","foreign","outside"])
def test_real_fd_hash_rejects_invalid_data(tmp_path,change):
    path=tmp_path/"model.gguf";data=b"synthetic bytes";path.write_bytes(data)
    sha=hashlib.sha256(data).hexdigest();size=len(data)
    if change=="sha":sha="f"*64
    if change=="length":size+=1
    if change=="symlink":
        target=tmp_path/"target";path.rename(target);path.symlink_to(target)
    if change=="missing":path.unlink()
    if change=="foreign":
        assert not g.hash_file(path,tmp_path,os.geteuid()+1,
                               expected_bytes=size,expected_sha256=sha)
        return
    if change=="outside":
        assert not g.hash_file(Path("/private/synthetic"),tmp_path,os.geteuid(),
                               expected_bytes=size,expected_sha256=sha)
        return
    assert not g.hash_file(path,tmp_path,os.geteuid(),
                           expected_bytes=size,expected_sha256=sha)


def test_nested_symlink_directory_rejected(tmp_path):
    target=tmp_path/"actual";target.mkdir()
    (target/"model.gguf").write_bytes(b"ok")
    alias=tmp_path/"alias";alias.symlink_to(target,target_is_directory=True)
    assert not g.hash_file(alias/"model.gguf",tmp_path,os.geteuid(),
                           expected_bytes=2,expected_sha256=hashlib.sha256(b"ok").hexdigest())


def test_wrong_host_main_always_exits_hold(monkeypatch,capsys):
    monkeypatch.setattr(g,"audit",lambda:{"verdict":"HOLD","router_cutover_authorized":False})
    assert g.main()==78
    assert json.loads(capsys.readouterr().out)["router_cutover_authorized"] is False


def test_owner_pin_matches_guarded_canary_reference_without_executing_it():
    """Guard against silently diverging trusted SHA/size reference sources."""
    import ast
    source = (Path(__file__).resolve().parents[1] / "scripts"
              / "joyner_minicpm_guarded_canary.py")
    tree = ast.parse(source.read_text())
    expected = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id in ("EXPECTED_MODEL_BYTES", "EXPECTED_MODEL_SHA256")):
                    expected[target.id] = ast.literal_eval(node.value)
    assert expected == {
        "EXPECTED_MODEL_BYTES": g.PINNED_BYTES,
        "EXPECTED_MODEL_SHA256": g.PINNED_SHA256,
    }
