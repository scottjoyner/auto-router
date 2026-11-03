#!/usr/bin/env python3
"""Read-only, no-runtime-import ONNX GenAI RyzenAI readiness shadow.

This is NOT a model runner, NPU repair tool, route admission token, production
health check, or cryptographic witness. It only characterizes a local model
configuration and coarse error fingerprints from independently run experiments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

FORMAT = "ort_ryzenai_admission_shadow_v1"
UPSTREAM_SOURCE_SHA = "82a4438b88febf4dd1762506d6cd5f50c5231ee3"
MAX_CONFIG_BYTES = 16_384
PROVIDERS = frozenset({"RyzenAI", "CPU", "CUDA", "DML", "OpenVINO", "QNN"})
ARCHITECTURES = frozenset({"qwen2", "lfm2", "lfm3"})
ERROR_CODES = frozenset({
    "NPU_PROVIDER_LIBRARY_NOT_FOUND",
    "NPU_IOCTL_BAD_ADDRESS",
    "CPU_INVALID_GRAPH",
    "TIMEOUT",
    "OTHER_FAILURE",
    "NO_ATTEMPT",
})


class AdmissionShadowError(ValueError):
    """Invalid or ineligible source data; do not disclose the raw value."""


def _strict_pairs(items: list[tuple[str, Any]]) -> dict:
    output = {}
    for key, value in items:
        if key in output:
            raise AdmissionShadowError("duplicate JSON key")
        output[key] = value
    return output


def parse_config(payload: bytes) -> dict[str, Any]:
    """Accept a bounded GenAI config, refuse arbitrary or ambiguous schema."""
    if type(payload) is not bytes or not (2 <= len(payload) <= MAX_CONFIG_BYTES):
        raise AdmissionShadowError("invalid config byte length")
    try:
        cfg = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=_strict_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(AdmissionShadowError(
                             "nonfinite JSON constant")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdmissionShadowError("invalid UTF-8 JSON") from exc
    if type(cfg) is not dict or type(cfg.get("model")) is not dict:
        raise AdmissionShadowError("invalid GenAI config envelope")
    model = cfg["model"]
    model_type = model.get("type")
    if type(model_type) is not str or model_type not in ARCHITECTURES:
        raise AdmissionShadowError("unrecognized model type")
    decoder = model.get("decoder")
    if type(decoder) is not dict:
        raise AdmissionShadowError("missing decoder definition")
    session = decoder.get("session_options")
    if type(session) is not dict:
        raise AdmissionShadowError("missing provider session")
    declared = session.get("provider_options", [])
    if type(declared) is not list or len(declared) > 8:
        raise AdmissionShadowError("invalid declared provider list")
    providers = []
    for row in declared:
        if type(row) is not dict or len(row) != 1:
            raise AdmissionShadowError("invalid declared provider entry")
        name, options = next(iter(row.items()))
        if type(name) is not str or name not in PROVIDERS or type(options) is not dict:
            raise AdmissionShadowError("unknown provider or options shape")
        providers.append(name)
    if len(providers) != len(set(providers)):
        raise AdmissionShadowError("duplicate declared provider")
    model_version = hashlib.sha256(payload).hexdigest()
    return {
        "format": FORMAT,
        "upstream_source_commit": UPSTREAM_SOURCE_SHA,
        "model_config_sha256": model_version,
        "configured_model_architecture": model_type,
        "configured_providers": sorted(providers),
        "ryzenai_declared": "RyzenAI" in providers,
        "upstream_current_tree_lists_lfm3": True,
        "installed_runtime_lfm3_executed": False,
        "model_file_load_verified": False,
        "npu_device_initialization_verified": False,
        "actual_generation_verified": False,
        "cpu_fallback_verified": False,
        "actual_prompt_tokens": None,
        "actual_generated_tokens": None,
        "attested_runtime_identity": False,
        "evidence_independent_custody": False,
        "production_routing_eligible": False,
        "production_gate": "HOLD",
    }


def safe_config_read(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute() or path.name != "genai_config.json":
        raise AdmissionShadowError("absolute genai_config.json required")
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise AdmissionShadowError("symlink config directory or file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                     getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise AdmissionShadowError("cannot read config") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or not 2 <= meta.st_size <= MAX_CONFIG_BYTES:
            raise AdmissionShadowError("invalid config file type/size")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            buf = handle.read(MAX_CONFIG_BYTES + 1)
        if len(buf) > MAX_CONFIG_BYTES:
            raise AdmissionShadowError("config exceeds bound")
        return buf
    finally:
        os.close(fd)


def classify_failure(error_text: Any, *, exit_code: int | None) -> str:
    """Sanitize error data; never return strings/paths from runtime stderr."""
    if exit_code is None and error_text is None:
        return "NO_ATTEMPT"
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise AdmissionShadowError("invalid process exit status")
    if type(error_text) is not str or len(error_text) > 16_384:
        raise AdmissionShadowError("invalid diagnostic text")
    if exit_code == 0:
        return "NO_ATTEMPT"  # success alone is not an attested model generation
    message = error_text.lower()
    if "drm_ioctl_amdxdna_create_bo" in message and (
        "err=-14" in message or "bad address" in message
    ):
        return "NPU_IOCTL_BAD_ADDRESS"
    if "libonnxruntime_providers_ryzenai.so" in message and (
        "cannot open shared object" in message or "no such file" in message
    ):
        return "NPU_PROVIDER_LIBRARY_NOT_FOUND"
    if "invalid model" in message and "node" in message:
        return "CPU_INVALID_GRAPH"
    if exit_code in (124, 137):
        return "TIMEOUT"
    return "OTHER_FAILURE"


def device_hints(*, accel_device: Path = Path("/dev/accel/accel0"),
                 driver: Path = Path("/sys/module/amdxdna")) -> dict:
    """Device presence is a *hint* only, not proof of a working runtime."""
    try:
        mode = accel_device.lstat().st_mode
        char_present = stat.S_ISCHR(mode)
    except OSError:
        char_present = False
    try:
        driver_present = driver.is_dir() and not driver.is_symlink()
    except OSError:
        driver_present = False
    return {"amdxdna_module_present":driver_present,
            "accel_character_device_present":char_present,
            "npu_ioctl_functional":None}


def summarize(config: bytes, *, failure: str = "NO_ATTEMPT",
              hints: dict | None = None) -> dict:
    if failure not in ERROR_CODES or type(failure) is not str:
        raise AdmissionShadowError("invalid classified failure code")
    report=parse_config(config)
    report["observed_failure_class"]=failure
    if hints is None:
        hints={"amdxdna_module_present":False,
               "accel_character_device_present":False,
               "npu_ioctl_functional":None}
    if type(hints) is not dict or set(hints) != {
        "amdxdna_module_present","accel_character_device_present","npu_ioctl_functional"
    }:
        raise AdmissionShadowError("invalid device hints")
    if (type(hints["amdxdna_module_present"]) is not bool or
        type(hints["accel_character_device_present"]) is not bool or
        hints["npu_ioctl_functional"] is not None):
        raise AdmissionShadowError("invalid device evidence")
    report.update(hints)
    return report


def main(argv: list[str] | None = None) -> int:
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config",type=Path,required=True)
    cli.add_argument("--allow-read",action="store_true")
    cli.add_argument("--observed-failure",choices=sorted(ERROR_CODES),
                     default="NO_ATTEMPT",help="operator-provided, UNATTESTED failure class")
    args=cli.parse_args(argv)
    if not args.allow_read:
        print("source access refused; --allow-read required",file=sys.stderr)
        return 2
    try:
        result=summarize(safe_config_read(args.config),
                         failure=args.observed_failure,hints=device_hints())
    except (AdmissionShadowError,OSError,TypeError,ValueError):
        print("untrusted GenAI configuration refused; production unchanged",file=sys.stderr)
        return 3
    print(json.dumps(result,sort_keys=True,separators=(",",":"),ensure_ascii=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
