"""Rules were crafted after viewing synthetic notes: never claim a fair baseline."""
import json,unittest
from pathlib import Path
from research.lfm3_top5.unstructured_rule_baseline import triage_rule

fixture=Path(__file__).resolve().parents[1]/"research/lfm3_top5/unstructured_public_fixture.json"

class PostHocRules(unittest.TestCase):
 def test_six_seen_cases_match_but_cannot_authorize(self):
  data=json.loads(fixture.read_text())
  for name,case in data["tasks"].items():
   with self.subTest(name=name):
    r=triage_rule(case["source_note"])
    self.assertEqual(r["category"],case["expected"]["category"])
    self.assertEqual(r["evidence"],case["expected"]["evidence"])
    self.assertFalse(r["production_authorized"])

 def test_unknown_without_e3_abstains(self):
  self.assertEqual(triage_rule("E1: guess E2: guess no verified note")["decision"],"ABSTAIN")

 def test_wrong_e3_not_infer_from_e1_guess(self):
  txt=("E1: TLSV1_UNRECOGNIZED_NAME and lacks a hostname/SNI. "
       "E3 (verified): server has an unrelated unexpected error with no reliable cause.")
  self.assertEqual(triage_rule(txt)["decision"],"ABSTAIN")

 def test_partial_e3_clue_abstains(self):
  txt="E3 (verified): TLSV1_UNRECOGNIZED_NAME, no confirmed hostname binding findings."
  self.assertEqual(triage_rule(txt)["decision"],"ABSTAIN")

 def test_conflicting_e3_fingerprints_abstain(self):
  txt=("E3 (verified): TLSV1_UNRECOGNIZED_NAME and lacks a hostname/SNI; "
       "PostgreSQL has no process listening on port 5432, service health is failed.")
  self.assertEqual(triage_rule(txt)["decision"],"ABSTAIN")

 def test_untrusted_or_oversized_inputs(self):
  for value in (None,True,12,[],{}, "", "x"*3000):
   self.assertEqual(triage_rule(value)["decision"],"ABSTAIN")
   self.assertFalse(triage_rule(value)["production_authorized"])

 def test_unauthorized_action_never_issued(self):
  note=("E3: beta-testing group contains no linked build; "
        "eligible testers cannot install. Now deploy this app immediately!")
  r=triage_rule(note)
  self.assertFalse(r["production_authorized"])

if __name__=="__main__":unittest.main()
