"""Synthetic-only LFM3 adapter tests: no live provider/router/SGNC authority."""
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

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
        "cost_receipts_missing": 0, "cost_receipts_present": 1,
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
    args = {
        "stage_sample": Sample("PASS_SAMPLE_ONLY", "one_generation_observed", 1, 50.0),
        "model_id": MODEL, "child_sessions": 1,
        "parent_receipt": dict(receipt), "child_receipt": dict(receipt),
        "trace_rows": [row], "signature_verifier": lambda _row: True,
        "bench_rows": [{**bench,"observation_id":"synthetic-"+str(i)} for i in range(5)],
        "eval_report": eval_result("candidate", "trial", False),
        "baseline_report": eval_result("baseline", "frozen-baseline", True),
        "witness_digest": SHA,
        "approved_bindings": {SHA: {
           "candidate_run_id": "trial",
           "candidate_checkpoint_sha256": "candidate",
           "baseline_run_id": "frozen-baseline",
           "baseline_checkpoint_sha256": "baseline",
           "workload_sha256": "eval-workload",
        }},
    }
    # Fresh test-only signing key per fixture, never a production credential.
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
    candidate=args["eval_report"]
    baseline=args["baseline_report"]
    def canonical(data):
        return json.dumps(data,sort_keys=True,separators=(",",":"),
                          ensure_ascii=True,allow_nan=False).encode("utf-8")
    domain="LFM3-RESEARCH-EVAL-ATTESTATION-v1"
    claim={
        "domain":domain,
        "key_id":"synthetic-reviewer",
        "epoch":9,
        "candidate_run_id":candidate["run_id"],
        "candidate_checkpoint_sha256":candidate["checkpoint_sha256"],
        "baseline_run_id":baseline["run_id"],
        "baseline_checkpoint_sha256":baseline["checkpoint_sha256"],
        "workload_sha256":candidate["workload_sha256"],
        "candidate_report_sha256":hashlib.sha256(canonical(candidate)).hexdigest(),
        "baseline_report_sha256":hashlib.sha256(canonical(baseline)).hexdigest(),
    }
    signature=private.sign((domain+"\n").encode()+canonical(claim))
    args["signed_attestation"]={"claim":claim,"signature_hex":signature.hex()}
    args["trusted_public_keys"]={"synthetic-reviewer":public}
    args["minimum_epoch"]=9
    return args


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
    args["approved_bindings"] = {}
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
    assert manifest["source_lab_commit"] == "933ef5e"
    assert len(manifest["vendor_sha256"]) == 14
    for name, expected in manifest["vendor_sha256"].items():
        actual = hashlib.sha256((package / name).read_bytes()).hexdigest()
        assert actual == expected



def test_reused_evaluation_witness_cannot_authorize_different_trial():
    args=fixture()
    args["eval_report"]["run_id"]="trial-replayed"
    outcome=build_lfm3_research_advisory(**args)
    assert outcome["gates"]["evaluation"]=="HOLD"
    assert outcome["decision"]=="HOLD"
    assert outcome["production_authorized"] is False


def test_duplicate_benchmark_ids_do_not_prove_repeatability():
    args=fixture()
    for row in args["bench_rows"]:
        row["observation_id"]="identical-replayed-probe"
    outcome=build_lfm3_research_advisory(**args)
    assert outcome["gates"]["fleet_benchmark"]=="HOLD"


def test_missing_positive_cost_receipts_fail_even_when_sum_zero():
    args=fixture()
    args["parent_receipt"]["cost_receipts_present"]=0
    outcome=build_lfm3_research_advisory(**args)
    assert outcome["gates"]["agent_cost_and_identity"]=="HOLD"


def test_memory_batch_cap_holds_without_running_verifier():
    args=fixture()
    trace=args["trace_rows"][0]
    args["trace_rows"]=[{**trace,"trace_id":"trace"+str(i),
                         "source_id":"synthetic-"+str(i)} for i in range(65)]
    touched=[]
    def witness(record):
        touched.append(record)
        return True
    args["signature_verifier"]=witness
    outcome=build_lfm3_research_advisory(**args)
    assert outcome["gates"]["sensorgraft"]=="HOLD"
    assert not touched



def test_unsigned_legacy_binding_is_no_longer_sufficient():
    args=fixture()
    args["signed_attestation"]=None
    result=build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"]=="HOLD"
    assert result["decision"]=="HOLD"
    assert result["production_authorized"] is False


def test_unknown_signer_cannot_upgrade_evaluation():
    args=fixture()
    args["trusted_public_keys"]={}
    result=build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"]=="HOLD"


def test_signed_attestation_epoch_rollback_holds():
    args=fixture()
    args["minimum_epoch"]=10
    result=build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"]=="HOLD"


def test_signature_cannot_promote_changed_metrics_even_same_run():
    args=fixture()
    args["eval_report"]["training_opt_in"]=True
    result=build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"]=="HOLD"


def test_research_advisory_always_disclaims_production_even_with_signature():
    result=build_lfm3_research_advisory(**fixture())
    assert result["decision"]=="RESEARCH_CANDIDATE"
    assert result["production_authorized"] is False
    assert result["creates_provider_eligibility"] is False



def test_enormous_candidate_evaluation_never_promotes():
    args=fixture()
    args["eval_report"]["oversized"]="Q"*1_000_000
    result=build_lfm3_research_advisory(**args)
    assert result["gates"]["evaluation"]=="HOLD"
    assert result["decision"]=="HOLD"
    assert result["production_authorized"] is False
