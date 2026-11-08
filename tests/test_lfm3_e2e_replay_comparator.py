"""Synthetic-only test suite for LFM3 source-only vs model-assisted replay.

No real model answers/identifiers. Tests make unsafe or false evidence
ineligible while allowing safely rejected model outputs to score zero.
"""
import copy
import hashlib
import unittest

from research.lfm3_top5.e2e_replay_comparator import (
    NODES, GATES, compare_replay, REPLAY_SEED,
)
from research.lfm3_top5.quality_task_audit import canonical
from test_lfm3_quality_task_audit import sample


def evaluate(manifest=None, receipts=None, *, nodes=None, gates=None, **kwargs):
    if manifest is None and receipts is None:
        manifest, receipts = sample(correct=True)
    return compare_replay(
        manifest, receipts,
        source_nodes=copy.deepcopy(NODES) if nodes is None else nodes,
        source_gates=copy.deepcopy(GATES) if gates is None else gates,
        **kwargs,
    )


def replace_answer(data, mode, block, task, answer, correct):
    stage = data[(mode, block, task)]
    stage["oracle_pass_count"] = 2 if correct else 0
    for row in stage["requests"]:
        row["full_public_synthetic_answer"] = answer
        row["response_sha256"] = hashlib.sha256(answer.encode()).hexdigest()
        row["response_first_100_chars"] = answer[:100]
        row["semantic_oracle_pass"] = correct


class Counter:
    def __init__(self, step=1000):
        self.value = 0
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


class OfflineReplay(unittest.TestCase):
    def test_all_correct_still_never_authorized(self):
        result = evaluate(repeats=3, warmup=1, clock_ns=Counter())
        self.assertEqual(result["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        self.assertEqual(result["historical_unique_requests"], 24)
        self.assertEqual(result["performance_sample_count"], 144)
        self.assertEqual(result["verified_gate_decision"], "HOLD")
        self.assertFalse(result["source_only_requires_model_call"])
        self.assertFalse(result["release_gate_result_derived_from_model"])
        self.assertFalse(result["quality_eligible_for_production_routing"])
        self.assertFalse(result["production_authorized"])
        self.assertFalse(result["source_independently_attested"])

    def test_per_mode_per_task_has_four_unique_historical_responses(self):
        result = evaluate(repeats=2, warmup=0, clock_ns=Counter())
        for task in ("gate", "nodes"):
            for mode in ("cpu", "vulkan6", "vulkan8"):
                x = result["source_only_and_assisted_replay"][task][mode]
                self.assertEqual(x["historical_http_request_count"], 4)
                self.assertEqual(x["source_only_correct_results"], 4)
                self.assertEqual(x["strict_model_exact_successes"], 4)
                self.assertEqual(x["source_only_local_validation"]["samples"], 8)
                self.assertEqual(x["model_assisted_local_validation"]["samples"], 8)
                self.assertEqual(x["source_only_local_validation"]["median_microseconds"], 1)
                self.assertFalse(x["model_contribution_required_for_source_only_success"])
                self.assertFalse(x["production_authorized"])

    def test_incorrect_model_answers_are_quality_fail_not_corrupt_receipt(self):
        m, data = sample(correct=False)
        result = evaluate(m, data, repeats=2, warmup=0, clock_ns=Counter())
        self.assertEqual(result["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        for task in ("gate", "nodes"):
            for mode in ("cpu", "vulkan6", "vulkan8"):
                x = result["source_only_and_assisted_replay"][task][mode]
                self.assertEqual(x["strict_model_exact_successes"], 0)
                self.assertEqual(x["source_only_correct_results"], 4)
                if task == "nodes":
                    self.assertEqual(x["model_source_verified_memberships"], 0)

    def test_unordered_nodes_may_be_validated_but_never_exact_credit(self):
        m, data = sample()
        unordered = canonical({
            "hold_or_fail": ["xwing", "joyner", "optiplex"],
            "healthy": ["x1-370", "deathstar", "destroyer"],
            "total": 6,
        })
        for mode in ("cpu", "vulkan6", "vulkan8"):
            for b in (1, 2):
                replace_answer(data, mode, b, "nodes", unordered, False)
        r = evaluate(m, data, repeats=1, warmup=0, clock_ns=Counter())
        self.assertEqual(r["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        for mode in ("cpu", "vulkan6", "vulkan8"):
            x = r["source_only_and_assisted_replay"]["nodes"][mode]
            self.assertEqual(x["model_source_verified_memberships"], 4)
            self.assertEqual(x["strict_model_exact_successes"], 0)
            self.assertFalse(x["production_authorized"])

    def test_wrong_node_membership_is_safe_zero_not_fabricated(self):
        m, data = sample()
        wrong = canonical({
            "hold_or_fail": ["xwing", "joyner", "destroyer"],
            "healthy": ["x1-370", "deathstar", "optiplex"],
            "total": 6,
        })
        replace_answer(data, "vulkan8", 1, "nodes", wrong, False)
        r = evaluate(m, data, repeats=1, warmup=0, clock_ns=Counter())
        self.assertEqual(r["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        self.assertEqual(r["source_only_and_assisted_replay"]["nodes"]["vulkan8"]["model_source_verified_memberships"], 2)

    def test_model_gate_pass_claim_does_not_override_source_hold(self):
        m, data = sample(correct=False)
        fake = canonical({"decision": "PASS", "failed_gates": [], "passed_gates": ["ci", "source"]})
        for mode in ("cpu", "vulkan6", "vulkan8"):
            for b in (1, 2):
                replace_answer(data, mode, b, "gate", fake, False)
        r = evaluate(m, data, repeats=2, warmup=1, clock_ns=Counter())
        self.assertEqual(r["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        self.assertEqual(r["verified_gate_decision"], "HOLD")
        self.assertFalse(r["release_gate_result_derived_from_model"])
        for mode in ("cpu", "vulkan6", "vulkan8"):
            x = r["source_only_and_assisted_replay"]["gate"][mode]
            self.assertEqual(x["strict_model_exact_successes"], 0)
            self.assertEqual(x["model_release_gate_exact_correct"], 0)

    def test_missing_gate_snapshot_blocks_entire_comparison(self):
        m, data = sample()
        states = copy.deepcopy(GATES)
        del states["tls"]
        self.assertEqual(evaluate(m, data, gates=states)["decision"], "HOLD")

    def test_changed_source_status_does_not_silently_change_oracle(self):
        m, data = sample()
        states = copy.deepcopy(NODES)
        states["xwing"] = "PASS"
        self.assertEqual(evaluate(m, data, nodes=states)["decision"], "HOLD")
        states = copy.deepcopy(GATES)
        states["signer"] = "PASS"
        self.assertEqual(evaluate(m, data, gates=states)["decision"], "HOLD")

    def test_missing_stage_denies_metrics(self):
        m, data = sample()
        del data[("vulkan6", 2, "gate")]
        self.assertEqual(evaluate(m, data)["decision"], "HOLD")

    def test_tampered_model_response_sha_denies_metrics(self):
        m, data = sample()
        data[("cpu", 1, "nodes")]["requests"][0]["response_sha256"] = "e" * 64
        self.assertEqual(evaluate(m, data)["decision"], "HOLD")

    def test_reported_model_quality_must_be_recomputed(self):
        m, data = sample()
        data[("cpu", 2, "gate")]["oracle_pass_count"] = 0
        self.assertEqual(evaluate(m, data)["decision"], "HOLD")

    def test_replay_clock_must_advance(self):
        self.assertEqual(
            evaluate(repeats=1, warmup=0, clock_ns=lambda: 0)["decision"], "HOLD")
        self.assertEqual(
            evaluate(repeats=1, warmup=0, clock_ns=lambda: -1)["decision"], "HOLD")
        self.assertEqual(
            evaluate(repeats=1, warmup=0, clock_ns=lambda: "time")["decision"], "HOLD")

    def test_unusable_clock_failure_fails_closed(self):
        def bad():
            raise RuntimeError("timer unavailable")
        self.assertEqual(
            evaluate(repeats=1, warmup=0, clock_ns=bad)["decision"], "HOLD")

    def test_sampling_limits_prevent_unbounded_bench(self):
        for count in (0, -1, 513, True, 5.0, "64"):
            self.assertEqual(evaluate(repeats=count)["decision"], "HOLD")
        for count in (-1, 33, True):
            self.assertEqual(evaluate(warmup=count)["decision"], "HOLD")

    def test_stored_request_durations_are_not_claimed_live_e2e(self):
        r = evaluate(repeats=1, warmup=0, clock_ns=Counter())
        self.assertFalse(r["historical_http_and_local_validation_measured_in_same_session"])
        self.assertFalse(r["full_end_to_end_latency_measured"])
        self.assertFalse(r["energy_cost_or_concurrent_service_load_included"])
        self.assertFalse(r["process_startup_included"])
        self.assertFalse(r["benchmark_can_prove_model_improves_deterministic_task"])
        self.assertTrue(r["source_only_and_assisted_replay"]["gate"]["cpu"][
            "reconstruction_is_NOT_live_e2e_latency"])

    def test_validation_timing_is_locally_recorded_but_not_model_latency(self):
        r = evaluate(repeats=2, warmup=1, clock_ns=Counter(step=7000))
        self.assertEqual(r["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        item = r["source_only_and_assisted_replay"]["nodes"]["cpu"]
        self.assertEqual(item["source_only_local_validation"]["median_microseconds"], 7)
        self.assertEqual(item["model_assisted_local_validation"]["median_microseconds"], 7)
        self.assertGreater(item["historical_http_total_ms"], 0)
        self.assertGreater(item["reconstructed_model_plus_guard_total_ms_proxy"],
                           item["historical_http_total_ms"])

    def test_duplicate_json_keys_do_not_increase_model_correctness(self):
        m, data = sample()
        forged = '{"total":6,"total":6,"healthy":["deathstar","destroyer","x1-370"],"hold_or_fail":["joyner","optiplex","xwing"]}'
        replace_answer(data, "vulkan8", 2, "nodes", forged, False)
        r = evaluate(m, data, repeats=1, warmup=0, clock_ns=Counter())
        self.assertEqual(r["decision"], "PASS_OFFLINE_TWO_PATH_REPLAY_RESEARCH_ONLY")
        self.assertEqual(r["source_only_and_assisted_replay"]["nodes"]["vulkan8"]["model_source_verified_memberships"], 2)

    def test_unsafe_historical_model_capacity_denies_replay(self):
        m, data = sample()
        data[("vulkan8", 1, "gate")]["preflight"]["memory_psi_avg10"] = 4.16
        self.assertEqual(evaluate(m, data)["decision"], "HOLD")

    def test_untrusted_inputs_never_issue_authorization(self):
        m, data = sample()
        for invalid in (None, [], {}, True, "bad"):
            a = evaluate(invalid, data)
            b = evaluate(m, invalid)
            self.assertEqual(a["decision"], "HOLD")
            self.assertEqual(b["decision"], "HOLD")
            self.assertFalse(a["production_authorized"])

if __name__ == "__main__":
    unittest.main()
