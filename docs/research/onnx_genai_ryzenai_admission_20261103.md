# Starred ONNX Runtime GenAI × Ryzen AI: local runtime-admission gate

**Date:** November 3, 2026, America/New_York  
**Status:** research-only / **production NO-GO**  
**Scope:** x1-370 read-only environment inspection and bounded synthetic runtime trials

## Candidate selection

Public GitHub stars under `scottjoyner` remained **1,068**
(11 pages, last page 68) on November 3, 2026.

Selected Microsoft [ONNX Runtime GenAI](https://github.com/microsoft/onnxruntime-genai),
MIT, upstream exact commit
`82a4438b88febf4dd1762506d6cd5f50c5231ee3`,
because the pinned
[`src/models/model_type.h`](https://github.com/microsoft/onnxruntime-genai/blob/82a4438b88febf4dd1762506d6cd5f50c5231ee3/src/models/model_type.h)
includes `lfm3` in its LLM types and the
`ModelType::IsLFM2()` family. The upstream
[`src/models/lfm2.cpp`](https://github.com/microsoft/onnxruntime-genai/blob/82a4438b88febf4dd1762506d6cd5f50c5231ee3/src/models/lfm2.cpp)
implements convolution/attention-state support and rejects
`RewindTo()` unless prior conv-state inputs are replayed.

**Important limitations:** this is *current pinned source-tree*
architecture recognition, not a tested LFM3 ONNX export,
confirmed model artifact, accelerated AMD GPU path, or
proof installed AMD RyzenAI package has these upstream commits.
The upstream README's hardware table places native **AMD GPU**
acceleration on its roadmap, notwithstanding CPU support.

Other relevant star:
[FlashML-org/FreeToken](https://github.com/FlashML-org/FreeToken)
offers MoE expert streaming, but its listed GPU support currently
targets NVIDIA RTX 30/40/50; do not infer ROCm/890M/R9700
performance or safe swap-on-host behavior.

## Actual x1-370 inventory (read-only)

| Component | Observed |
| --- | --- |
| Kernel | `7.0.0-42-generic` |
| Driver/module | `amdxdna` loaded, **in-tree kernel module** under `/lib/modules/7.0.0-42-generic/kernel/`, not DKMS |
| NPU device | `/dev/accel/accel0` exists as character device; PCI vendor 0x1022, device 0x17f0, rev 0x10 |
| XRT userspace | 2.25.37 (`xrt-base`, `xrt-npu` packages present) |
| `xrt-smi examine` (read-only) | **Exit 0; device detected** as RyzenAI-npu4, architecture aie2p, topology 6x8 |
| AMD NPU firmware | **1.1.2.64**, as reported by XRT |
| AMD SDK and Python environment | Already installed; nothing downloaded |
| `onnxruntime-genai-ryzenai` | `0.14.0`, import reports `0.14.0-dev` |
| `onnxruntime-vitisai` | `1.27.0` |
| `ryzenai-dynamic-dispatch` | `1.8.0` |
| `ryzenai_onnx_utils` | `1.8.0` |
| Local model package | `Qwen2.5-0.5B-Instruct`, approx 911 MiB |
| Model `genai_config.json` | `model.type=qwen2`; `provider_options` names `RyzenAI` |
| Config SHA-256 | `770125fa66eb10e047d85ed4861cf5063b81859c60b30f37a0f4d9938688e2c8` |
| Model architecture actually tested | **Qwen2**, not LFM3 |

Model path, raw model binaries and proprietary SDK files were
**not committed or published**. The config's hash is a local
untrusted-byte fingerprint; it does **not** attest a published
model package or runtime signer.

Resource snapshot prior to testing: around **67 GiB available
RAM**, swap **7.4/8 GiB used** and 1.76 1-minute load average.
Because the machine does other useful work, experiments were
limited to one synthetic short prompt per run, CPU priority
`nice +15`, two CPU affinity cores, a hard 32–65 second
timeout and isolated temporary working directories.
No weights or service state were changed intentionally.

## Actual bounded trial results

| Trial | Observation | Result |
| --- | --- | --- |
| Original direct `og.Model(path)` in disposable working directory | Provider library not found relative to temporary CWD | Failed before model initialization; **environment lookup issue** |
| Same native RyzenAI attempt after linking **existing installed** library in temporary CWD and setting `LD_LIBRARY_PATH` | `DRM_IOCTL_AMDXDNA_CREATE_BO IOCTL failed (err=-14): Bad address` | **NPU initialization FAILED** |
| `Config.clear_providers(); append_provider("cpu")` | CPU provider string not recognized by the installed GenAI API | Invalid override; no generation |
| `Config.clear_providers()` (default CPU fallback) | ONNX graph rejected as `This is an invalid model. In Node ... If` | **CPU fallback FAILED** for this *particular NPU-optimized ONNX artifact* |

**Actual generated tokens: 0.** None of these trials reached
a completed model generation. There are **no** valid
time-to-first-token, decoding-throughput, task-quality or
model-accuracy results.

A follow-up read-only XRT probe ran after the failed model trials:
with process-local `/opt/xilinx/xrt/setup.sh`, `xrt-smi examine`
returned exit 0 and identified the NPU and firmware above.
**The NPU is enumerated correctly; the model-specific BO ioctl
still failed.** AMD's [Linux installation guide](https://ryzenai.docs.amd.com/en/latest/linux.html)
uses `xrt-smi examine` as a driver installation check and requires
compatible XRT, NPU drivers and runtime. Its [application
compatibility notes](https://ryzenai.docs.amd.com/en/latest/app_development.html)
explicitly call for APU-type and driver/EP checks before execution.
These are discovery/compatibility checks, not a substitute for a
real successful model generation.

An `amdxdna` character device and world-accessible device
node prove **only presence**, not a compatible firmware,
compiler/runtime/kernel ABI or functioning NPU buffer
allocation. `err=-14` identifies a failing ioctl but **does
not independently prove its root cause**. Avoid guessing that
permissions, the current kernel, firmware or graph format
alone caused it.

The CPU graph rejection is **not** evidence that Microsoft
ONNX Runtime GenAI generally lacks CPU functionality.

## Safe code added

`research/onnx_genai_ryzenai_shadow.py` is a **standard-library
only**, opt-in, read-only model configuration classifier:

- Refuses file access unless `--allow-read` is supplied.
- Requires absolute non-symlink
  `genai_config.json`, bounded to 16,384 bytes and
  opened with no-follow protection. Refuses duplicate
  JSON keys, nonfinite numbers, bad encoding, invalid
  decoder/provider list and unknown model types.
- Emits only safe model architecture/provider names,
  config SHA-256, device/driver presence booleans and
  **operator-supplied untrusted error-class labels**.
- Defines explicit sanitized error categories for missing
  provider library, `DRM_IOCTL_AMDXDNA_CREATE_BO` EFAULT,
  graph invalidity, timeout, or unknown failure.
- Does **not** import ONNX Runtime, execute subprocesses,
  use network access, start a model, read model weights,
  print raw stderr, export config content or change
  any Auto-Router provider.
- A config file, model support claim, character device,
  or even an untrusted `exit_code=0` always keeps
  `production_routing_eligible=false`,
  `actual_generation_verified=false`,
  `attested_runtime_identity=false`,
  `evidence_independent_custody=false`,
  `production_gate=HOLD`.
- No external output is used as a policy authorization;
  the supplied failure label is **not a signed receipt**.

`tests/test_onnx_genai_ryzenai_shadow.py` provides **45/45**
local standard-library tests on `x1-370` at commit
`a7581bc6809c3045daf1983d4e5a628d3241f670`,
covering schema, nulls, fake loaded-state evidence,
malformed/duplicate JSON, URI/path/symlink, source size,
NPU device hints, sanitized error fingerprints, CLI no-read
default, lack of network/process imports and hard HOLD.
AST checks passed. On the real config the static report
correctly emitted **RyzenAI configured, driver + accel
character present, NPU_IOCTL_BAD_ADDRESS, production HOLD**.

All test scaffolding, temporary model-runtime scripts and
temporary library symlinks were cleaned up on process exit.

## Next admissible steps

1. Check **vendor-supported firmware, installed Ryzen AI
   SDK and kernel `amdxdna` driver ABI/version matrix**.
   Do not reflash, reload modules, reinstall drivers or
   restart an active host as a speculative fix.
2. Read-only inspect the current **signed vendor model
   package and compiler build metadata**, including
   `RyzenAI`/XRT device architecture compatibility.
   The current model was built as a Ryzen NPU graph;
   CPU cannot be assumed as a drop-in alternative.
3. Validate the runtime with a **known-good minimal
   NPU example built for this exact driver/runtime
   combination** under bounded resource controls,
   without use of private prompts.
4. Obtain or convert an **actual LFM3 ONNX model**
   separately under a versioned export/quantization
   contract, comparing reference generation against
   an approved baseline, before making LFM3-specific
   claims. Do not assume current 0.14.0 RyzenAI
   wheel supports the upstream pinned `lfm3` changes.
5. Only after successful NPU initialization and real
   bounded generation, require source-verified model
   identity, token counts, latency, task quality,
   authenticated route owners and signed SGNC
   receipt before *considering* route enablement.

**Decision: HOLD.** Additional available work in the
fleet should use already qualified inference endpoints;
advertised models and installed libraries are not
evidence that an inference backend is usable.
