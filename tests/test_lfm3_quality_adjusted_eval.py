"""Frozen synthetic adversarial quality-adjusted routing research cases.

No remote contacts or real host receipts. Model request duration alone is
NOT operational throughput. A safe capture without exact answers can pass
the infrastructure audit yet fail the application quality gate.
"""
from __future__ import annotations
import copy,hashlib,json,math,unittest

from research.lfm3_top5.quality_adjusted_eval import (
    NODES,GATES,assess_quality_adjusted
)
from research.lfm3_top5.quality_task_audit import canonical
from test_lfm3_quality_task_audit import sample

def run(manifest=None,receipts=None,*,source_nodes=None,source_gates=None):
    if manifest is None and receipts is None:
        manifest,receipts=sample(correct=True)
    return assess_quality_adjusted(
        manifest,receipts,
        source_nodes=copy.deepcopy(NODES) if source_nodes is None else source_nodes,
        source_gates=copy.deepcopy(GATES) if source_gates is None else source_gates)

def apply_answer(receipts,mode,block,task,answer,exact):
    stage=receipts[(mode,block,task)]
    stage["oracle_pass_count"]=2 if exact else 0
    for row in stage["requests"]:
        row["full_public_synthetic_answer"]=answer
        row["response_sha256"]=hashlib.sha256(answer.encode()).hexdigest()
        row["response_first_100_chars"]=answer[:100]
        row["semantic_oracle_pass"]=exact

def unordered_node_answer():
    return canonical({
        "healthy":["x1-370","deathstar","destroyer"],
        "hold_or_fail":["xwing","joyner","optiplex"],
        "total":6,
    })

class QualityAdjustedRate(unittest.TestCase):
    def test_all_correct_is_research_not_production(self):
        r=run()
        self.assertEqual(r["decision"],"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY")
        self.assertEqual(r["verified_request_count"],24)
        self.assertEqual(r["raw_model_exact_successes"],24)
        self.assertEqual(r["quality_adjusted_task_rates"]["nodes"]["cpu"]["source_grounded_node_canonical_successes"],4)
        self.assertTrue(r["quality_adjusted_task_rates"]["nodes"]["vulkan8"][
            "source_verified_node_correct_per_observed_http_second_upper_bound"]>0)
        self.assertEqual(r["source_derived_release_gate_decision"],"HOLD")
        self.assertFalse(r["quality_adjusted_routing_candidate_admitted"])
        self.assertFalse(r["production_authorized"])

    def test_all_incorrect_completions_are_zero_not_fast_success(self):
        m,rs=sample(correct=False)
        r=run(m,rs)
        self.assertEqual(r["decision"],"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY")
        self.assertEqual(r["raw_model_exact_successes"],0)
        for mode in ("cpu","vulkan6","vulkan8"):
            for task in ("nodes","gate"):
                self.assertEqual(
                    r["quality_adjusted_task_rates"][task][mode][
                        "strict_model_correct_per_observed_http_second"],0)
            self.assertEqual(
                r["quality_adjusted_task_rates"]["nodes"][mode][
                    "source_verified_node_correct_per_observed_http_second_upper_bound"],0)

    def test_unsorted_but_source_correct_nodes_normalize_only_after_verification(self):
        m,rs=sample()
        text=unordered_node_answer()
        for mode in ("cpu","vulkan6","vulkan8"):
            for block in (1,2):
                apply_answer(rs,mode,block,"nodes",text,False)
        r=run(m,rs)
        self.assertEqual(r["decision"],"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY")
        self.assertEqual(r["raw_model_exact_successes"],12)
        for mode in ("cpu","vulkan6","vulkan8"):
            node=r["quality_adjusted_task_rates"]["nodes"][mode]
            self.assertEqual(node["raw_exact_model_successes"],0)
            self.assertEqual(node["source_verified_model_node_memberships"],4)
            self.assertEqual(node["source_grounded_node_canonical_successes"],4)
            self.assertEqual(node["strict_model_correct_per_observed_http_second"],0)
            self.assertGreater(node["source_verified_node_correct_per_observed_http_second_upper_bound"],0)
        self.assertFalse(r["production_authorized"])

    def test_wrong_node_membership_not_credited_after_source_verification(self):
        m,rs=sample()
        text=canonical({
            "healthy":["x1-370","deathstar","xwing"],
            "hold_or_fail":["joyner","optiplex","destroyer"],"total":6
        })
        for block in (1,2):
            apply_answer(rs,"vulkan8",block,"nodes",text,False)
        r=run(m,rs)
        self.assertEqual(r["decision"],"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY")
        self.assertEqual(r["quality_adjusted_task_rates"]["nodes"]["vulkan8"]["source_verified_node_correct_per_observed_http_second_upper_bound"],0)
        self.assertFalse(r["production_authorized"])

    def test_missing_source_snapshot_blocks_derived_rate(self):
        m,rs=sample()
        source=dict(NODES);source.pop("joyner")
        self.assertEqual(run(m,rs,source_nodes=source)["decision"],"HOLD")

    def test_external_source_cannot_rewrite_frozen_expected_answer(self):
        m,rs=sample()
        source=dict(NODES);source["xwing"]="PASS"
        self.assertEqual(run(m,rs,source_nodes=source)["decision"],"HOLD")
        g=dict(GATES);g["tls"]="PASS"
        self.assertEqual(run(m,rs,source_gates=g)["decision"],"HOLD")

    def test_expired_or_unknown_gate_must_not_be_assumed_pass(self):
        m,rs=sample()
        g=dict(GATES);g["signer"]="EXPIRED"
        r=run(m,rs,source_gates=g)
        self.assertEqual(r["decision"],"HOLD")
        self.assertFalse(r["production_authorized"])

    def test_missing_block_fails_entire_rate_calculation(self):
        m,rs=sample()
        rs.pop(("vulkan8",2,"nodes"))
        self.assertEqual(run(m,rs)["decision"],"HOLD")

    def test_invalid_model_receipt_cannot_be_used_to_sweep_accuracy_failure(self):
        m,rs=sample()
        rs[("cpu",1,"gate")]["requests"][0]["semantic_oracle_pass"]=False
        self.assertEqual(run(m,rs)["decision"],"HOLD")

    def test_untrusted_reply_digest_is_not_counted(self):
        m,rs=sample()
        rs[("vulkan6",1,"nodes")]["requests"][0]["response_sha256"]="f"*64
        self.assertEqual(run(m,rs)["decision"],"HOLD")

    def test_nonfinite_wall_time_is_denied(self):
        for bad in (float("nan"),float("inf"),float("-inf"),0,-1,True):
            m,rs=sample()
            rs[("vulkan8",1,"nodes")]["requests"][0]["wall_ms"]=bad
            self.assertEqual(run(m,rs)["decision"],"HOLD")

    def test_forged_decode_speed_no_production(self):
        m,rs=sample()
        for row in rs[("vulkan8",1,"nodes")]["requests"]:
            row["timings"]["predicted_per_second"]=10000.
        self.assertEqual(run(m,rs)["decision"],"HOLD")

    def test_explicitly_excludes_startup_and_validation_and_costs(self):
        r=run()
        self.assertEqual(r["metric_denominator"],"sum_sequential_observed_model_HTTP_wall_seconds_only")
        self.assertFalse(r["quality_adjusted_task_rates"]["nodes"]["cpu"]["full_end_to_end_latency_with_validation_measured"])
        self.assertTrue(r["rate_is_optimistic_research_only_upper_bound"])
        self.assertFalse(r["free_provider_billing_verified"])
        self.assertFalse(r["source_custody_independently_signed"])

    def test_release_gate_model_output_never_acquires_release_authority(self):
        r=run()
        for mode in ("cpu","vulkan6","vulkan8"):
            t=r["quality_adjusted_task_rates"]["gate"][mode]
            self.assertEqual(t["model_release_gate_successes_for_routing"],0)
            self.assertIsNone(t["source_verified_node_correct_per_observed_http_second_upper_bound"])
        self.assertFalse(r["model_release_gate_decisions_authoritative"])

    def test_duplicate_json_key_cannot_increase_correctness(self):
        m,rs=sample()
        original=canonical({"decision":"HOLD","failed_gates":["database","signer","tls"],"passed_gates":["ci","source"]})
        forged=original[:-1]+',"decision":"HOLD"}'
        for block in (1,2):
            apply_answer(rs,"cpu",block,"gate",forged,False)
        r=run(m,rs)
        self.assertEqual(r["decision"],"PASS_QUALITY_ADJUSTED_DESCRIPTIVE_RESEARCH_ONLY")
        self.assertEqual(r["quality_adjusted_task_rates"]["gate"]["cpu"]["raw_exact_model_successes"],0)

    def test_bad_input_types_hold(self):
        m,rs=sample()
        for bad in (None,True,[],"malformed",{}):
            r=run(bad,rs)
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["production_authorized"])
            r=run(m,bad)
            self.assertEqual(r["decision"],"HOLD")
            self.assertFalse(r["production_authorized"])

if __name__=="__main__":
    unittest.main()
