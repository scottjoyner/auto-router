"""Adversarial deterministic normalization (no user files or live data)."""
import json,unittest
from research.lfm3_top5.quality_canonical_guard import node_summary_guard,release_gate_guard

NODES=("x1-370","xwing","joyner","deathstar","destroyer","optiplex")
SNAP={
 "x1-370":"PASS","xwing":"HOLD","joyner":"HOLD",
 "deathstar":"PASS","destroyer":"PASS","optiplex":"FAIL",
}
NODE_MODEL={
 "hold_or_fail":["xwing","joyner","optiplex"],
 "healthy":["x1-370","deathstar","destroyer"],"total":6,
}
GATES=("source","ci","tls","database","signer")
GATE_STATES={"source":"PASS","ci":"PASS","tls":"FAIL","database":"FAIL","signer":"HOLD"}
GATE_EXACT={
 "decision":"HOLD","failed_gates":["database","signer","tls"],
 "passed_gates":["ci","source"],
}

def node(x,source=SNAP,required=NODES):
 return node_summary_guard(json.dumps(x) if type(x) is dict else x,
                           source_snapshot=source,required_nodes=required)
def gate(x,source=GATE_STATES,required=GATES):
 return release_gate_guard(json.dumps(x) if type(x) is dict else x,
                           source_gates=source,required_gates=required)

class CanonicalGuards(unittest.TestCase):
 def test_unordered_source_correct_nodes_can_be_normalized(self):
  got=node(NODE_MODEL)
  self.assertEqual(got["decision"],"PASS_SEMANTIC_NORMALIZATION_RESEARCH_ONLY")
  self.assertEqual(got["canonical_output"]["hold_or_fail"],["joyner","optiplex","xwing"])
  self.assertEqual(got["canonical_output"]["healthy"],["deathstar","destroyer","x1-370"])
  self.assertFalse(got["source_independently_signed"])
  self.assertFalse(got["production_authorized"])

 def test_nodes_missing_one_denied(self):
  wrong=dict(NODE_MODEL,hold_or_fail=["xwing","joyner"])
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_nodes_new_unknown_denied(self):
  wrong=dict(NODE_MODEL,healthy=NODE_MODEL["healthy"]+["unknown"])
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_nodes_duplicate_denied(self):
  wrong=dict(NODE_MODEL,healthy=["deathstar","deathstar","x1-370"])
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_nodes_cross_class_denied(self):
  wrong=dict(NODE_MODEL,hold_or_fail=["xwing","joyner","optiplex","x1-370"])
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_nodes_wrong_status_membership_denied(self):
  wrong=dict(NODE_MODEL,
    hold_or_fail=["x1-370","joyner","optiplex"],
    healthy=["xwing","deathstar","destroyer"])
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_nodes_total_boolean_not_int(self):
  wrong=dict(NODE_MODEL,total=True)
  self.assertEqual(node(wrong)["decision"],"HOLD")

 def test_invalid_source_must_hold(self):
  source=dict(SNAP);source["joyner"]="UNKNOWN"
  self.assertEqual(node(NODE_MODEL,source)["decision"],"HOLD")
  source=dict(SNAP);source.pop("xwing")
  self.assertEqual(node(NODE_MODEL,source)["decision"],"HOLD")
  self.assertEqual(node(NODE_MODEL,SNAP,("xwing","xwing"))["decision"],"HOLD")

 def test_node_duplicate_json_key_and_nan_denied(self):
  self.assertEqual(node('{"total":6,"total":6,"healthy":[],"hold_or_fail":[]}')["decision"],"HOLD")
  self.assertEqual(node('{"total":NaN,"healthy":[],"hold_or_fail":[]}')["decision"],"HOLD")

 def test_node_untrusted_think_and_wrong_shape(self):
  for text in ('<think>foo</think>'+json.dumps(NODE_MODEL),
               '[]','null','{"total":6}',"x"*2100):
   self.assertEqual(node(text)["decision"],"HOLD")

 def test_correct_gate_source_holds_without_prod_authority(self):
  got=gate(GATE_EXACT)
  self.assertEqual(got["decision"],"HOLD")
  self.assertEqual(got["canonical_output"],GATE_EXACT)
  self.assertTrue(got["model_exactly_matches_source"])
  self.assertFalse(got["production_authorized"])

 def test_wrong_model_gate_entries_not_promoted(self):
  x={"decision":"HOLD",
     "failed_gates":["database gate","TLS gate","signer gate"],
     "passed_gates":["PASS","CI gate PASS","Source gate PASS"]}
  got=gate(x)
  self.assertFalse(got["model_exactly_matches_source"])
  self.assertEqual(got["canonical_output"]["failed_gates"],["database","signer","tls"])
  self.assertFalse(got["production_authorized"])

 def test_model_trying_to_upgrade_decision_ignored(self):
  x=dict(GATE_EXACT,decision="PASS")
  got=gate(x)
  self.assertEqual(got["decision"],"HOLD")
  self.assertFalse(got["model_exactly_matches_source"])

 def test_gate_missing_or_unknown_source_denied(self):
  source=dict(GATE_STATES);source.pop("tls")
  self.assertEqual(gate(GATE_EXACT,source)["decision"],"HOLD")
  source=dict(GATE_STATES);source["tls"]="OK"
  self.assertEqual(gate(GATE_EXACT,source)["decision"],"HOLD")

 def test_gate_all_pass_still_production_false(self):
  source={key:"PASS" for key in GATES}
  got=gate({"decision":"PASS","failed_gates":[],"passed_gates":sorted(GATES)},
           source)
  self.assertEqual(got["decision"],"PASS")
  self.assertTrue(got["model_exactly_matches_source"])
  self.assertFalse(got["source_independently_signed"])
  self.assertFalse(got["production_authorized"])

 def test_gate_duplicate_keys_never_match(self):
  text='{"decision":"PASS","decision":"HOLD","failed_gates":[],"passed_gates":[]}'
  self.assertFalse(gate(text)["model_exactly_matches_source"])

 def test_gate_untrusted_types(self):
  for x in (None,True,[],1,"this is not json", "[]" ):
   got=gate(x)
   self.assertFalse(got["model_exactly_matches_source"])
   self.assertFalse(got["production_authorized"])

if __name__=="__main__":unittest.main()
