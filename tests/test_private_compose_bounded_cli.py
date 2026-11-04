"""Synthetic portable byte-cap tests for private release Compose preflight.

Only Python subprocess fixtures; never contact the real Docker daemon,
production Compose project, router, remote hosts or model servers.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts/verify_private_compose_candidate_readonly.py"
spec = importlib.util.spec_from_file_location("bounded_private_compose", SOURCE)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def simulate(script: str, *, stdin: bytes | None = None) -> bytes | None:
    return gate._bounded_compose_config(
        [sys.executable, "-B", "-c", script], input=stdin
    )


def test_bounded_json_config_succeeds():
    assert simulate('print(\'{"services":{}}\')') == b'{"services":{}}\n'


def test_stdin_synthetic_override_uses_private_process_pipe_only():
    assert simulate(
        "import sys; data=sys.stdin.buffer.read();sys.stdout.buffer.write(data)",
        stdin=b'{"safe":"synthetic"}',
    ) == b'{"safe":"synthetic"}'


def test_stdout_exact_maximum_allowed():
    max_bytes = gate._MAX_CONFIG_BYTES
    assert simulate(
        f"import sys;sys.stdout.buffer.write(b'x'*{max_bytes})"
    ) == b"x" * max_bytes


def test_stdout_one_byte_over_maximum_is_rejected():
    limit = gate._MAX_CONFIG_BYTES + 1
    assert simulate(
        f"import sys;sys.stdout.buffer.write(b'x'*{limit})"
    ) is None


def test_streamed_flood_denied_during_read_not_post_capture():
    assert simulate(
        "import sys;sys.stdout.buffer.write(b'x' * 9000000);"
        "sys.stdout.flush()"
    ) is None


def test_nonzero_child_status_is_sanitized():
    assert simulate(
        "import sys;sys.stderr.write('SECRET_FAKE_ABC');sys.exit(9)"
    ) is None


def test_invalid_synthetic_overlay_stops_before_child_launch(monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError("should never launch")
    monkeypatch.setattr(gate.subprocess, "Popen", forbidden)
    assert simulate("print('{}')", stdin=b"x" * 5000) is None


def test_timeout_kills_child(monkeypatch):
    monkeypatch.setattr(gate, "_COMPOSE_CONFIG_TIMEOUT", 0.25)
    assert simulate("import time;time.sleep(8)") is None


def test_inherited_stdout_pipe_does_not_wait_indefinitely(monkeypatch):
    monkeypatch.setattr(gate, "_COMPOSE_CONFIG_TIMEOUT", 0.3)
    assert simulate(
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(8)']);"
        "print('{}',flush=True)"
    ) is None


def test_returned_private_data_is_never_copied_to_gate_receipt(monkeypatch, tmp_path):
    private = "SYNTHETIC_PRIVATE_CONFIG_SECRET_VALUE"
    config = tmp_path / "operator-compose.yaml"
    config.write_text("services: {}\n")
    def fake_config(_argv, **_kwargs):
        return (
            '{"services":{"llm-router":{"image":"sha256:' + "a" * 64
            + '","pull_policy":"never","environment":{"TOKEN":"'
            + private + '"}}}}'
        ).encode()
    monkeypatch.setattr(gate, "_bounded_compose_config", fake_config)
    report = gate.run_private_preflight(
        [str(config)], service="llm-router",
        expected_image_id="sha256:" + "a" * 64
    )
    assert report["decision"] == "PASS_PRIVATE_COMPOSE_STRUCTURE_ONLY"
    assert private not in repr(report)


def test_failed_synthetic_compose_fails_closed_without_child_text(monkeypatch, tmp_path):
    config = tmp_path / "operator-compose.yaml"
    config.write_text("services: {}\n")
    monkeypatch.setattr(gate, "_bounded_compose_config", lambda *_a, **_kw: None)
    result = gate.run_synthetic_overlay_check([str(config)], service="llm-router")
    assert result["decision"] == "HOLD_PRIVATE_COMPOSE"
    assert result["reason_codes"] == ["PRIVATE_COMPOSE_CONFIG_UNAVAILABLE"]
