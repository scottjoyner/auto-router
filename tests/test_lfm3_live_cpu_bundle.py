"""Public synthetic replicas of the read-only five-request CPU stage evidence."""
import copy
import unittest

from research.lfm3_top5.benchmark_report import verify_local_stage_bundle

SHA = "a"*64
WORKLOAD = "b"*64


def fixture():
    rows = []
    meta = []
    for index in range(5):
        identifier = "chatcmpl-synthetic-"+str(index)
        rows.append({
            "observation_id":identifier,"node":"synthetic-node",
            "workload_sha256":WORKLOAD,"checkpoint_sha256":SHA,
            "quantization":"Q4_K_M","context_tokens":4096,
            "max_output_tokens":24,"sampling_hash":WORKLOAD,
            "prompt_tokens":16,"output_tokens":3,
            "prefill_ms":80.0,"decode_ms":40.0,"peak_ram_mb":850.0,
            "accelerator_verified":False
        })
        meta.append({
            "observation_id":identifier,"finish_reason":"stop","answer":"READY",
            "model":"lfm3-local-test",
            "usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20},
            "server_timings":{"prompt_n":16,"predicted_n":3,
                              "prompt_ms":80.0,"predicted_ms":40.0,
                              "prompt_per_second":200.0,"predicted_per_second":50.0},
            "end_to_end_ms":130.0,
        })
    return {"schema":"lfm3.real.cpu-stage.5x.v1",
        "scope":"loopback-only-public-synthetic-prompt",
        "requested_generations":5,"actual_generations_attempted":5,
        "result":"PASS_RESEARCH_ONLY",
        "production_authorized":False,"accelerator_verified":False,
        "independent_measurement_witness":False,
        "model_bytes":1099627808,
        "backend":"llama.cpp CPU-only -ngl 0",
        "model":"lfm3-local-test",
        "checkpoint_sha256":SHA,"workload_sha256":WORKLOAD,
        "rows":rows,"request_meta":meta
    }


_UNSPECIFIED = object()


class LiveStageBundleTests(unittest.TestCase):
    def check(self, data=_UNSPECIFIED, expected=SHA):
        if data is _UNSPECIFIED:
            data=fixture()
        return verify_local_stage_bundle(data,expected_checkpoint_sha256=expected)

    def test_five_complete_canonical_cpu_receipts_are_research_only(self):
        result=self.check()
        self.assertEqual(result["decision"],"PASS_RESEARCH_ONLY")
        self.assertEqual(result["observations"],5)
        self.assertFalse(result["accelerator_verified"])
        self.assertFalse(result["production_authorized"])

    def test_local_independent_digest_must_match_recorded_checkpoint(self):
        self.assertEqual(self.check(expected="c"*64)["decision"],"HOLD")
        self.assertEqual(self.check(expected="not-a-digest")["decision"],"HOLD")
        self.assertEqual(self.check(expected=True)["decision"],"HOLD")

    def test_duplicate_or_missing_request_id_is_not_five_observations(self):
        data=fixture()
        data["request_meta"][1]["observation_id"]=data["request_meta"][0]["observation_id"]
        self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["rows"][2]["observation_id"]=""
        self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_tampered_server_response_cannot_validate(self):
        data=fixture()
        data["request_meta"][2]["usage"]["total_tokens"]=21
        self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["request_meta"][1]["answer"]="No"
        self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["request_meta"][3]["model"]="another"
        self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_benchmark_row_must_match_primary_server_timings(self):
        data=fixture()
        data["rows"][4]["decode_ms"]=1.0
        self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["rows"][3]["output_tokens"]=20
        self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_unauthorized_claims_and_wrong_backend_are_held(self):
        for key in ("production_authorized","accelerator_verified",
                    "independent_measurement_witness"):
            data=fixture()
            data[key]=True
            self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["backend"]="llama.cpp GPU -ngl 99"
        self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_no_retries_or_synthetic_extra_rows(self):
        data=fixture()
        data["actual_generations_attempted"]=6
        self.assertEqual(self.check(data)["decision"],"HOLD")
        data=fixture()
        data["rows"].append(copy.deepcopy(data["rows"][0]))
        self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_native_server_reported_decode_rate_is_separate_from_derived_rate(self):
        result=self.check()
        self.assertEqual(result["server_reported_decode_tps_p50"],50.0)
        self.assertEqual(result["server_reported_prefill_tps_p50"],200.0)
        # P4's derived token/time rate need not equal the server's native
        # decode TPS, and must not be promoted as an exact hardware SLO.
        self.assertNotEqual(result["benchmark_summary"]["decode_tokens_per_sec_p50"],
                            result["server_reported_decode_tps_p50"])

    def test_native_reported_rates_must_be_positive_and_finite(self):
        for field in ("prompt_per_second","predicted_per_second"):
            data=fixture()
            del data["request_meta"][0]["server_timings"][field]
            self.assertEqual(self.check(data)["decision"],"HOLD")
            data=fixture()
            data["request_meta"][0]["server_timings"][field]=float("nan")
            self.assertEqual(self.check(data)["decision"],"HOLD")

    def test_missing_evidence_or_malformed_values_fail_closed(self):
        self.assertEqual(self.check({})["decision"],"HOLD")
        self.assertEqual(self.check(None)["decision"],"HOLD")
        data=fixture()
        data["request_meta"][0]["server_timings"]["prompt_ms"]=float("nan")
        self.assertEqual(self.check(data)["decision"],"HOLD")


if __name__=="__main__":
    unittest.main()
