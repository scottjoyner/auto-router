"""Synthetic-only regression tests: no model requests, no GPU allocations."""
import importlib.util
import json
from pathlib import Path
import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/joyner_lfm3_passive_gpu_observer.py"
spec = importlib.util.spec_from_file_location("joyner_lfm3_passive", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_proc(tmp_path, *, tick=111, client=42, ns=500,
              pci=module.PCI, cgroup=module.SERVICE):
    root = tmp_path / "proc"
    pid = root / "123"
    (pid / "fdinfo").mkdir(parents=True, exist_ok=True)
    # Fields after (comm) begin with stat field 3; starttime is field 22.
    (pid / "stat").write_text("123 (a name) " + " ".join(["S"] * 19 + [str(tick)]) + "\n")
    (pid / "cgroup").write_text("0::/user.slice/app.slice/" + cgroup + "\n")
    info = (f"drm-client-id:\t{client}\n"
            f"drm-pdev:\t{pci}\n"
            f"drm-engine-compute:\t{ns} ns\n"
            f"drm-memory-vram:\t2946048 KiB\n"
            f"drm-memory-gtt:\t1075000 KiB\n")
    for fd in ("11", "12"):
        (pid / "fdinfo" / fd).write_text(info)
    return root


def make_receipt(tmp_path, timestamp="2026-11-13T20:35:20+00:00"):
    target = tmp_path / "probe.jsonl"
    obj = {"schema": module.SCHEMA, "node": "joyner", "model": module.MODEL,
           "probe_count": 8, "successful_requests": 8,
           "timestamp_utc": timestamp}
    target.write_text(json.dumps(obj) + "\n")
    return target


def test_reads_deduplicated_gpu_counter(tmp_path):
    proc = make_proc(tmp_path)
    assert module.process_identity(123, proc) == 111
    assert module.drm_compute(123, proc) == (500, 2946048, 1075000)


def test_snapshot_and_comparison_fails_closed(tmp_path):
    proc = make_proc(tmp_path)
    p = make_receipt(tmp_path)
    a = module.snapshot(123, proc, p)
    assert a["start_ticks"] == 111
    assert a["compute_ns"] == 500
    assert module.compare(a, a)["correlation"] == "inconclusive"
    assert module.compare(a, a)["release_gate"] == "HOLD"


def test_correlation_not_token_attribution(tmp_path):
    proc = make_proc(tmp_path)
    r = make_receipt(tmp_path)
    a = module.snapshot(123, proc, r)
    make_proc(tmp_path, ns=800)
    make_receipt(tmp_path, "2026-11-13T21:05:20+00:00")
    b = module.snapshot(123, proc, r)
    x = module.compare(a, b)
    assert x["new_receipt"]
    assert x["compute_delta_ns"] == 300
    assert x["correlation"].startswith("consistent_with")
    assert x["proof_of_per_token_attribution"] is False
    assert x["inference_requests_made"] == 0
    assert x["release_gate"] == "HOLD"


@pytest.mark.parametrize("kind", ["wrong_service", "wrong_gpu", "negative", "missing_client"])
def test_untrusted_gpu_snapshot_denied(tmp_path, kind):
    proc = make_proc(tmp_path)
    pid = proc / "123"
    if kind == "wrong_service":
        (pid / "cgroup").write_text("0::/user.slice/not-lfm3.service\n")
        with pytest.raises(ValueError, match="cgroup"):
            module.process_identity(123, proc)
        return
    info = pid / "fdinfo" / "11"
    raw = info.read_text()
    if kind == "wrong_gpu":
        raw = raw.replace(module.PCI, "0000:10:00.0")
        (pid / "fdinfo" / "12").write_text(raw)
    elif kind == "negative":
        raw = raw.replace("500 ns", "-1 ns")
    else:
        raw = raw.replace("drm-client-id:\t42", "drm-client-id:\t")
    info.write_text(raw)
    with pytest.raises(ValueError):
        module.drm_compute(123, proc)


def test_pid_mismatch_or_regressed_counter_rejected(tmp_path):
    proc = make_proc(tmp_path)
    receipt = make_receipt(tmp_path)
    a = module.snapshot(123, proc, receipt)
    b = dict(a)
    b["start_ticks"] = 222
    with pytest.raises(ValueError, match="start-time"):
        module.compare(a, b)
    b = dict(a)
    b["compute_ns"] = 499
    with pytest.raises(ValueError, match="regressed"):
        module.compare(a, b)


def test_receipt_identity_fail_closed(tmp_path):
    file = make_receipt(tmp_path)
    assert module.receipt_time(file) == "2026-11-13T20:35:20+00:00"
    obj = json.loads(file.read_text())
    obj["model"] = "unknown"
    file.write_text(json.dumps(obj) + "\n")
    with pytest.raises(ValueError, match="model"):
        module.receipt_time(file)


def test_receipt_no_direct_model_access(tmp_path):
    proc = make_proc(tmp_path)
    p = make_receipt(tmp_path)
    s = module.snapshot(123, proc, p)
    assert "api_key" not in s
    assert "prompt" not in s
    assert "content" not in s


def test_cli_wait_default_and_bound_are_explicit():
    source = PATH.read_text()
    assert 'type=int, default=45' in source
    assert '0 <= args.wait_seconds <= 60' in source
    assert 'wait-seconds must be 0..60' in source
