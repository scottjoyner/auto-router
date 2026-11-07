"""Synthetic-only LFM3 adapter tests: no live provider/router/SGNC authority."""
import hashlib

from research.lfm3_top5 import (
    CREATES_PROVIDER_ELIGIBILITY,
    REQUIRES_SIGNED_ADMISSION,
    RESEARCH_ONLY,
)
from research.lfm3_top5.bridge import build_lfm3_research_advisory
from research.lfm3_top5.inference_contract import Sample

MODEL = "openrouter/recursive/flint-v1.0:free"
SHA = "a" * 64


def fixture():
    receipt = {
        "models": [MODEL],
        "session_reported_cost": 0,
        "cost_receipts_missing": 0,
        "token_usage_reported": 24,
        "finish_reasons": ["stop"],
    }
    text = "synthetic public test trace"
    row = {
        "trace_id": "trace1",
        "source_id": "synthetic-lab",
        "classification": "public_synthetic",
        "content": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "signature_checked_upstream": False,
    }
    bench = {
        "node": "x1-370", "workload_sha256": "workload",
        "checkpoint_sha256": "candidate", "quantization": "q4",
        "context_tokens": 512, "max_output_tokens": 32,
        "sampling_hash": "sampling", "prompt_tokens": 20,
        "output_tokens": 4, "prefill_ms": 20, "decode_ms": 50,
        "peak_ram_mb": 1000,
    }
    def eval_result(checkpoint, run, frozen):
        return {
            "run_id": run,
            "workload_sha256": "eval-workload",
            "checkpoint_sha256": checkpoint,
            "evidence_class": "independently_witnessed_real",
            "exact_head_ci": True,
            "frozen": frozen,
            "metrics": {
                k: {"pass": 20, "total": 20}
                for k in ("format", "exact_match", "tool_schema",
                          "provenance_rejection", "latency")
            },
        }
    return {
        "stage_sample": Sample("PASS_SAMPLE_ONLY", "one_generation_observed", 1, 50.0),
        "model_id": MODEL, "child_sessions": 1,
        "parent_receipt": dict(receipt), "child_receipt": dict(receipt),
        "trace_rows": [row], "signature_verifier": lambda _row: True,
        "bench_rows": [dict(bench) for _ in range(5)],
        "eval_report": eval_result("candidate", "trial", False),
        "baseline_report": eval_result("baseline", "frozen-baseline", True),
        "witness_digest": SHA, "approved_witnesses": frozenset([SHA]),
    }


def test_positive_lab_evidence_only_advisory():
    result = build_lfm3_research_advisory(**fixture())
    assert result["decision"] == "RESEARCH_CANDIDATE"
    assert len(result["gates"]) == 5
    assert all(v == "PASS_RESEARCH_ONLY" for v in result["gates"].values())
    assert result["production_authorized"] is False
    assert result["creates_provider_eligibility"] is False
    assert result["signed_admission_required"] is True
    assert result["live_requests_sent"] == 0
    assert RESEARCH_ONLY
    assert not CREATES_PROVIDER_ELIGIBILITY
    assert REQUIRES_SIGNED_ADMISSION


def test_stage_mismatch_blocks_advisory():
    args = fixture()
    args["stage_sample"] = Sample("HOLD", "model_identity_mismatch", 1, 50.0)
    result = build_lfm3_research_advisory(**args)
    assert result["gates"]["runtime"] == "HOLD"
    assert result["decision"] == "HOLD"
    assert result["production_authorized"] is False


def test_unverified_free_cost_blocks_advisory():
    args = fixture()
    args["child_receipt"]["session_reported_cost"] = False
    result = build_lfm3_research_advisory(**args)
    assert result["gates"]["agent_cost_and_identity"] == "HOLD"
    assert result["decision"] == "HOLD"


def test_noncryptographic_untrusted_row_is_not_admitted_without_verifier():
    args = fixture()
    args["trace_rows"][0]["signature_checked_upstream"] = True
    args["signature_verifier"] = lambda _row: False
    result = build_lfm3_research_advisory(**args)
    assert result["gates"]["sensorgraft"] == "HOLD"
    assert result["production_authorized"] is False


def test_single_synthetic_benchmark_cannot_qualify():
    args = fixture()
    args["bench_rows"] = args["bench_rows"][:1]
    result = build_lfm3_research_advisory(**args)
    assert result["gates"]["fleet_benchmark"] == "HOLD"


def test_missing_external_evaluation_witness_cannot_promote():
    args = fixture()
    args["approved_witnesses"] = frozenset()
    result = build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"] == "HOLD"


def test_advisory_never_bypasses_signed_provider_admission_even_with_fake_proofs():
    args = fixture()
    for _ in range(3):
        result = build_lfm3_research_advisory(**args)
        assert result["production_authorized"] is False
        assert result["creates_provider_eligibility"] is False
        assert result["live_requests_sent"] == 0



def test_research_sources_cannot_import_production_runtime():
    import ast
    from pathlib import Path
    package = Path(__file__).resolve().parents[1] / "research" / "lfm3_top5"
    assert package.is_dir()
    assert "src" not in package.parts
    forbidden = ("auto_router", "subprocess", "socket", "urllib.request")
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        for value in imports:
            assert not any(value == f or value.startswith(f + ".") for f in forbidden), (
                source.name, value
            )


def test_vendored_sources_equal_frozen_lab_commit_hashes():
    import hashlib
    import json
    from pathlib import Path
    package = Path(__file__).resolve().parents[1] / "research" / "lfm3_top5"
    manifest = json.loads((package / "VENDOR_PROVENANCE.json").read_text())
    assert manifest["source_lab_commit"] == "0d447c6"
    assert len(manifest["vendor_sha256"]) == 5
    for name, expected in manifest["vendor_sha256"].items():
        actual = hashlib.sha256((package / name).read_bytes()).hexdigest()
        assert actual == expected
