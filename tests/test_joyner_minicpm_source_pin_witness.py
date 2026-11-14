"""Offline canonical MiniCPM source model and process custody tests.

No network, model or credential reads except small disposable synthetic fixtures.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import joyner_minicpm_source_pin_witness as pin
from scripts import joyner_minicpm_guarded_canary as guarded


def fake_proc(tmp_path, model, *, uid, port="1234", extra=b""):
    root = tmp_path / "proc"
    root.mkdir()
    p = root / "1825"
    p.mkdir()
    p.joinpath("cmdline").write_bytes(
        ("/usr/bin/llama-server\0--port\0" + port
         + "\0--model\0" + str(model) + "\0").encode() + extra
    )
    p.joinpath("status").write_text(f"Name:\tllama-server\nUid:\t{uid} {uid} {uid} {uid}\n")
    return root


def fixture_model(tmp_path, monkeypatch, contents=b"small-synthetic-gguf"):
    path = tmp_path / "canonical-model.gguf"
    path.write_bytes(contents)
    monkeypatch.setattr(pin, "MODEL", path)
    return path


def test_pinned_constants_are_in_sync_with_guarded_canary_source():
    assert pin.EXPECTED_SHA256 == guarded.EXPECTED_MODEL_SHA256
    assert pin.EXPECTED_BYTES == guarded.EXPECTED_MODEL_BYTES
    assert pin.SOURCE_PORT == 1234


def test_file_hash_and_stat_accept_only_pinned_bytes(tmp_path, monkeypatch):
    model = fixture_model(tmp_path, monkeypatch)
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    report = pin.verify_file(
        path=model, expected_size=model.stat().st_size, expected_sha256=digest
    )
    assert report == {
        "file_verified": True, "reason": "pinned_source_file_verified"
    }


@pytest.mark.parametrize("invalid", ["wrong_size", "wrong_sha", "symlink", "missing"])
def test_invalid_source_never_grants_file_custody(tmp_path, monkeypatch, invalid):
    model = fixture_model(tmp_path, monkeypatch)
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    size = model.stat().st_size
    if invalid == "wrong_size":
        size += 1
    elif invalid == "wrong_sha":
        digest = "a" * 64
    elif invalid == "symlink":
        target = tmp_path / "other"
        model.rename(target)
        model.symlink_to(target)
    elif invalid == "missing":
        model.unlink()
    report = pin.verify_file(path=model, expected_size=size, expected_sha256=digest)
    assert report["file_verified"] is False


def test_noncanonical_file_cannot_be_read(tmp_path, monkeypatch):
    path = fixture_model(tmp_path, monkeypatch)
    other = tmp_path / "unapproved"
    other.write_text("SYNTHETIC-SECRET")
    result = pin.verify_file(other, expected_size=1, expected_sha256="a" * 64)
    assert result == {"file_verified": False, "reason": "noncanonical_model_path"}


def test_process_binding_requires_exact_model_path_and_owner(tmp_path, monkeypatch):
    model = fixture_model(tmp_path, monkeypatch)
    root = fake_proc(tmp_path, model, uid=pin.os.geteuid())
    assert pin.process_binding(root, model)[0] is True
    assert pin.process_binding(root, model)[1] == 1825
    assert pin.process_binding(root, tmp_path / "other")[0] is False


def test_process_binding_rejects_mismatched_owner(tmp_path, monkeypatch):
    model = fixture_model(tmp_path, monkeypatch)
    root = fake_proc(tmp_path, model, uid=pin.os.geteuid() + 1)
    bound, pid, reason = pin.process_binding(root, model)
    assert not bound and pid is None
    assert reason == "source_process_owner_mismatch"


def test_process_binding_rejects_wrong_port(tmp_path, monkeypatch):
    model = fixture_model(tmp_path, monkeypatch)
    root = fake_proc(tmp_path, model, uid=pin.os.geteuid(), port="1236")
    assert pin.process_binding(root, model)[0] is False


def test_process_binding_rejects_ambiguous_duplicate_process(tmp_path, monkeypatch):
    model = fixture_model(tmp_path, monkeypatch)
    root = fake_proc(tmp_path, model, uid=pin.os.geteuid())
    other = root / "1827"
    other.mkdir()
    for name in ("cmdline", "status"):
        (other / name).write_bytes((root / "1825" / name).read_bytes())
    assert pin.process_binding(root, model)[2] == "source_process_not_unique"


def test_unavailable_process_inventory_does_not_pass(tmp_path):
    bound, pid, why = pin.process_binding(tmp_path / "missing", pin.MODEL)
    assert not bound
    assert why in ("process_inventory_unavailable", "noncanonical_model_path")


def test_wrong_host_skips_all_readers():
    def forbidden(*a, **k):
        raise AssertionError("private model or process read on wrong host")
    report = pin.audit(hostname="x1-370", process_reader=forbidden,
                       file_reader=forbidden)
    assert report["verdict"] == "HOLD"
    assert not report["observer_host_verified"]
    assert not report["canonical_process_verified"]
    assert not report["disk_file_matches_guarded_canary_pinned_sha256"]


def test_successful_source_pin_still_cannot_approve_cutover():
    calls = []
    def process():
        calls.append("process")
        return True, 1825, "source_process_bound"
    def file():
        calls.append("file")
        return {"file_verified": True, "reason": "pinned_source_file_verified"}
    result = pin.audit(hostname="joyner", process_reader=process, file_reader=file)
    assert calls == ["process", "file"]
    assert result["disk_file_matches_guarded_canary_pinned_sha256"] is True
    assert result["guarded_process_weights_verified"] is False
    assert result["resident_loaded_weights_verified"] is False
    assert result["router_cutover_authorized"] is False
    assert result["service_change_authorized"] is False
    assert result["source_model_requests_sent"] is False
    assert result["credential_values_read"] is False


def test_process_unverified_prevents_disk_read():
    def forbidden():
        raise AssertionError("file accessed after failed process witness")
    result = pin.audit(
        hostname="joyner",
        process_reader=lambda: (False, None, "source_process_not_unique"),
        file_reader=forbidden,
    )
    assert not result["disk_file_matches_guarded_canary_pinned_sha256"]


def test_main_emits_json_and_holds(monkeypatch, capsys):
    monkeypatch.setattr(pin, "audit", lambda: {"verdict": "HOLD"})
    assert pin.main() == 78
    assert json.loads(capsys.readouterr().out)["verdict"] == "HOLD"
