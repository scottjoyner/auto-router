#!/usr/bin/env python3
"""Opt-in, fail-closed 4K RyzenAI research canary; never admits production routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_4K_CONFIG_SHA256 = "5dec97a007abb3aab6cd4bd48df0c06ad217e4a2c872c0dcc72241019334f184"
EXPECTED_ONNX_GRAPH_SHA256 = "14b6de9461b38c00898788da3efed93a3ea5aa8ef597a43cd409cea8de86cbbf"
EXPECTED_ONNX_DATA_SHA256 = "8e3104b796839b77c57d59cc5cabce0b2bae189f74452c564bd2fb207686eee1"
MAX_CONFIG_BYTES = 16_384
MAX_PACKAGE_BYTES = 6 * 1024**3
MIN_AVAILABLE_KIB = 25_000_000
DEVICE = Path("/dev/accel/accel0")
CHILD = r"""
import hashlib
import json
import os
import time

import onnxruntime_genai as og

t0 = time.monotonic()
model = og.Model(os.environ["RESEARCH_MODEL_DIR"])
load_s = time.monotonic() - t0
tokenizer = og.Tokenizer(model)
prompt = tokenizer.encode("Hello.")
requested = int(os.environ["RESEARCH_OUTPUT_TOKENS"])
params = og.GeneratorParams(model)
params.set_search_options(max_length=len(prompt) + requested, do_sample=False)
generator = og.Generator(model, params)
generator.append_tokens(prompt)
t0 = time.monotonic()
steps = 0
while steps < requested and not generator.is_done():
    generator.generate_next_token()
    steps += 1
elapsed_s = time.monotonic() - t0
sequence = generator.get_sequence(0)
generated = [int(x) for x in sequence[len(prompt):]]
npu_fds = 0
for fd in os.listdir("/proc/self/fd"):
    try:
        npu_fds += os.readlink("/proc/self/fd/" + fd) == "/dev/accel/accel0"
    except OSError:
        pass
result = {
    "model_init_seconds": round(load_s, 4),
    "prompt_tokens": len(prompt),
    "generated_tokens": len(generated),
    "decode_seconds": round(elapsed_s, 4),
    "npu_fds": npu_fds,
    "first_four_sha256": hashlib.sha256(
        str(generated[:4]).encode("utf-8")
    ).hexdigest(),
}
print("NPU_CANARY_JSON=" + json.dumps(result, sort_keys=True), flush=True)
"""


class ResearchGateError(ValueError):
    """Refuse unrecognized or unsafe research conditions."""


def _strict_pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ResearchGateError("ambiguous JSON configuration")
        result[key] = value
    return result


def _hash_regular_file(path: Path) -> str:
    """Bounded-memory hash of a non-symlink regular file; refuse unsafe types."""
    if path.is_symlink():
        raise ResearchGateError("model data symlink refused")
    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ResearchGateError("model file is not regular")
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        raise ResearchGateError("model file read failed") from exc


def inspect(
    model_dir: Path,
    expected_hash: str = EXPECTED_4K_CONFIG_SHA256,
    expected_graph_hash: str = EXPECTED_ONNX_GRAPH_SHA256,
    expected_data_hash: str = EXPECTED_ONNX_DATA_SHA256,
) -> dict:
    if not model_dir.is_absolute() or any(
        component.is_symlink() for component in (model_dir, *model_dir.parents)
    ):
        raise ResearchGateError("canonical absolute model directory required")
    config_path = model_dir / "genai_config.json"
    if config_path.is_symlink():
        raise ResearchGateError("symlink configuration refused")
    try:
        fd = os.open(config_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 2 <= info.st_size <= MAX_CONFIG_BYTES:
                raise ResearchGateError("invalid bounded configuration")
            raw = os.read(fd, MAX_CONFIG_BYTES + 1)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ResearchGateError("configuration read failed") from exc
    if len(raw) > MAX_CONFIG_BYTES or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ResearchGateError("unrecognized exact model configuration")
    try:
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
        assert type(config) is dict
        model = config["model"]
        options = model["decoder"]["session_options"]
        providers = options["provider_options"]
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise ResearchGateError("invalid model/provider configuration") from exc
    if (
        model.get("type") != "qwen2"
        or type(providers) is not list
        or len(providers) != 1
        or type(providers[0]) is not dict
        or set(providers[0]) != {"RyzenAI"}
        or type(providers[0]["RyzenAI"]) is not dict
        or providers[0]["RyzenAI"].get("hybrid_opt_token_backend") != "npu"
        or type(options.get("custom_ops_library")) is not str
        or type(options.get("vaip_config")) is not str
    ):
        raise ResearchGateError("model is not the verified 4K RyzenAI candidate")
    for name in ("model.onnx", "model.onnx.data", "README.md"):
        path = model_dir / name
        if path.is_symlink() or not path.is_file():
            raise ResearchGateError("required regular model file missing")
    if "npu_4K" not in (model_dir / "README.md").read_text()[:4096]:
        raise ResearchGateError("model package variant not confirmed")
    if _hash_regular_file(model_dir / "model.onnx") != expected_graph_hash:
        raise ResearchGateError("ONNX graph fingerprint mismatch")
    if _hash_regular_file(model_dir / "model.onnx.data") != expected_data_hash:
        raise ResearchGateError("external tensor fingerprint mismatch")
    return {
        "model_config_sha256": hashlib.sha256(raw).hexdigest(),
        "onnx_graph_sha256": expected_graph_hash,
        "onnx_external_data_sha256": expected_data_hash,
        "model_architecture": "qwen2",
        "ryzenai_backend_declared": True,
        "candidate_correction": "remove only session_options.custom_ops_library",
        "requires_regular_external_onnx_data": True,
        "actual_generated_tokens": None,
        "independent_custody": False,
        "production_routing_eligible": False,
        "production_gate": "HOLD",
    }


def _check_host() -> None:
    try:
        meminfo = Path("/proc/meminfo").read_text()
        available = next(
            int(line.split()[1])
            for line in meminfo.splitlines()
            if line.startswith("MemAvailable:")
        )
    except (OSError, ValueError, StopIteration) as exc:
        raise ResearchGateError("cannot determine available memory") from exc
    if available < MIN_AVAILABLE_KIB:
        raise ResearchGateError("insufficient isolated memory headroom")
    if not DEVICE.exists() or not stat.S_ISCHR(DEVICE.stat().st_mode):
        raise ResearchGateError("NPU character device unavailable")
    if not shutil.which("strace") or not shutil.which("fuser"):
        raise ResearchGateError("required NPU evidence instrumentation unavailable")
    probe = subprocess.run(["fuser", str(DEVICE)], capture_output=True, timeout=5, check=False)
    if probe.returncode != 1:
        raise ResearchGateError("NPU occupied or holder check uncertain")


def _validate_package_tree(model_dir: Path) -> int:
    size = 0
    for current, dirs, files in os.walk(model_dir, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink():
                raise ResearchGateError("model package symlink refused")
            if path.is_file():
                size += path.stat().st_size
                if size > MAX_PACKAGE_BYTES:
                    raise ResearchGateError("package exceeds research size bound")
    return size


def _venv_paths(venv: Path) -> tuple[Path, Path, list[str]]:
    if not venv.is_absolute() or venv.is_symlink():
        raise ResearchGateError("absolute runtime required")
    candidates = list(venv.glob("lib/python*/site-packages"))
    if len(candidates) != 1:
        raise ResearchGateError("unsupported Python runtime layout")
    site = candidates[0]
    python = venv / "bin/python"
    provider = venv / "deployment/lib/libonnxruntime_providers_ryzenai.so"
    paths = [
        venv / "deployment/lib",
        Path("/opt/xilinx/xrt/lib"),
        site / "lnx64.o/tools/peano/lib",
        venv / "onnxruntime/lib",
        site / "onnxruntime/capi",
        site / "voe/lib",
    ]
    if not python.exists() or not provider.is_file() or any(not d.is_dir() for d in paths):
        raise ResearchGateError("RyzenAI library paths incomplete")
    return python, provider, [str(d) for d in paths]


def execute(model_dir: Path, venv: Path, requested_tokens: int) -> dict:
    if not 1 <= requested_tokens <= 16:
        raise ResearchGateError("synthetic output-token limit exceeded")
    _check_host()
    package_bytes = _validate_package_tree(model_dir)
    python, provider, library_dirs = _venv_paths(venv)
    with tempfile.TemporaryDirectory(prefix="ryzenai-4k-research-") as root:
        staging = Path(root)
        if shutil.disk_usage(staging).free < package_bytes + 2 * 1024**3:
            raise ResearchGateError("insufficient scratch disk headroom")
        staged_model = staging / "model"
        shutil.copytree(model_dir, staged_model, symlinks=False)
        if _hash_regular_file(staged_model / "model.onnx") != EXPECTED_ONNX_GRAPH_SHA256:
            raise ResearchGateError("staged ONNX graph identity drift")
        if _hash_regular_file(staged_model / "model.onnx.data") != EXPECTED_ONNX_DATA_SHA256:
            raise ResearchGateError("staged external tensor identity drift")
        config_path = staged_model / "genai_config.json"
        config = json.loads(config_path.read_text(), object_pairs_hook=_strict_pairs)
        config["model"]["decoder"]["session_options"].pop("custom_ops_library")
        config_path.write_text(json.dumps(config, separators=(",", ":")))
        env = os.environ.copy()
        env.update(
            {
                "HOME": root,
                "XDG_CACHE_HOME": str(staging / "cache"),
                "PYTHONNOUSERSITE": "1",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "XILINX_XRT": "/opt/xilinx/xrt",
                "RYZEN_AI_INSTALLATION_PATH": str(venv),
                "RYZENAI_EP_PATH": str(provider),
                "RESEARCH_MODEL_DIR": str(staged_model),
                "RESEARCH_OUTPUT_TOKENS": str(requested_tokens),
                "LD_LIBRARY_PATH": ":".join(library_dirs),
            }
        )
        trace = staging / "npu_ioctl.trace"
        command = [
            "nice",
            "-n",
            "15",
            "strace",
            "-qq",
            "-f",
            "-y",
            "-e",
            "trace=ioctl",
            "-o",
            str(trace),
            str(python),
            "-c",
            CHILD,
        ]
        proc = subprocess.Popen(
            command,
            cwd=staging,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=70)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ResearchGateError("bounded canary deadline exceeded") from exc
        if proc.returncode != 0:
            raise ResearchGateError("canary process failed")
        markers = [
            line[len("NPU_CANARY_JSON=") :]
            for line in stdout.splitlines()
            if line.startswith("NPU_CANARY_JSON=")
        ]
        if len(markers) != 1:
            raise ResearchGateError("token evidence missing or ambiguous")
        try:
            tokens = json.loads(markers[0])
        except json.JSONDecodeError as exc:
            raise ResearchGateError("malformed token evidence") from exc
        if trace.stat().st_size > 10 * 1024**2:
            raise ResearchGateError("NPU ioctl evidence exceeds bound")
        with trace.open() as handle:
            npu_ioctl_success = sum(
                "/dev/accel/accel0" in line and "= 0" in line for line in handle
            )
        if (
            type(tokens.get("generated_tokens")) is not int
            or tokens["generated_tokens"] != requested_tokens
            or type(tokens.get("npu_fds")) is not int
            or tokens["npu_fds"] < 1
            or npu_ioctl_success < 1
        ):
            raise ResearchGateError("NPU generation proof insufficient")
        return {
            "research_canary_pass": True,
            "actual_prompt_tokens": tokens["prompt_tokens"],
            "actual_generated_tokens": tokens["generated_tokens"],
            "npu_device_fds": tokens["npu_fds"],
            "successful_npu_ioctls": npu_ioctl_success,
            "first_four_sha256": tokens["first_four_sha256"],
            "model_init_seconds": tokens["model_init_seconds"],
            "decode_seconds": tokens["decode_seconds"],
            "independent_custody": False,
            "production_routing_eligible": False,
            "production_gate": "HOLD",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--venv-dir", type=Path)
    parser.add_argument("--run-canary", action="store_true")
    parser.add_argument("--ack-research-npu-execution", action="store_true")
    parser.add_argument("--tokens", type=int, default=4)
    args = parser.parse_args(argv)
    if args.run_canary and (not args.ack_research_npu_execution or args.venv_dir is None):
        parser.error("--run-canary requires --ack-research-npu-execution and --venv-dir")
    try:
        report = inspect(args.model_dir)
        if args.run_canary:
            report.update(execute(args.model_dir, args.venv_dir, args.tokens))
    except (
        ResearchGateError,
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ):
        print("RyzenAI research preflight refused; production unchanged", file=sys.stderr)
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
