"""Frozen adversarial free-only provider routing inputs."""
import math
import unittest
from research.lfm3_top5.route_guard import recommend, classify_http, verify_free_receipt, Choice


class TestRouterUntrustedInputs(unittest.TestCase):
    def test_malformed_http_status_never_crashes_or_authorizes(self):
        for code in (None, False, True, "", "200", "500", [], {},
                     float("nan"), float("inf"), -1, 0, 600):
            result=classify_http(code)
            self.assertEqual(result.decision,"HOLD")
            self.assertIsNone(result.provider)

    def test_only_literal_200_is_sampling_success(self):
        self.assertEqual(classify_http(200).decision,"PASS_SAMPLE_ONLY")
        self.assertEqual(classify_http(429).decision,"HOLD")
        self.assertEqual(classify_http(401).decision,"HOLD")

    def test_malformed_route_collection_fails_closed(self):
        for value in (None, True, False, "free", 5, {}, (), ["junk"]):
            result=recommend(value,data_class="public_synthetic")
            self.assertEqual(result.decision,"HOLD")

    def test_route_cardinality_is_bounded_before_looping(self):
        route={"provider":"openrouter","model":"x:free","verified_zero_cost":True,
               "last_probe_status":"PASS","retry_after_seconds":0,
               "dynamic_pool":False,"explicit_id":True,
               "data_class":"public_synthetic"}
        result=recommend([route]*65,data_class="public_synthetic")
        self.assertEqual(result.decision,"HOLD")

    def test_ambiguous_concurrency_parameter_cannot_allow_routing(self):
        route={"provider":"openrouter","model":"x:free","verified_zero_cost":True,
               "last_probe_status":"PASS","retry_after_seconds":0,
               "dynamic_pool":False,"explicit_id":True,
               "data_class":"public_synthetic"}
        for busy in (None, 0, 1, "False", []):
            self.assertEqual(recommend([route],data_class="public_synthetic",
                                       in_flight=busy).decision,"HOLD")
        self.assertEqual(recommend([route],data_class="public_synthetic",
                                   in_flight=False).decision,"PASS_TO_SINGLE_PROBE")

    def test_unknown_requested_provider_or_model_cannot_match_missing_receipt(self):
        receipt={"provider":None,"model":None,"status":200,
                 "cost_usd":0,"completion_tokens":2}
        for provider,model in ((None,None),("", ""),("openrouter",None)):
            self.assertEqual(verify_free_receipt(provider,model,receipt).decision,"HOLD")


if __name__=="__main__":
    unittest.main()
