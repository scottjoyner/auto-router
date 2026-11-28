import unittest
from auto_router.recovery_acceptance import validate,expected_for,evaluate_recovery_response
D=[{'id':'D1','decoded':1,'expected':1800}]
S=[{'id':'S1','bytes':10,'boxes':['ftyp']}]
class AcceptanceTests(unittest.TestCase):
 def test_valid_damage(self): self.assertTrue(validate('damage',D,'{"D1":"PRESERVE"}')['accepted'])
 def test_semantic_failure(self): self.assertEqual(validate('damage',D,'{"D1":"REVIEW"}')['reason'],'SEMANTIC_MISMATCH')
 def test_zero(self): self.assertEqual(expected_for('damage',[dict(D[0],decoded=0)]),{'D1':'PRESERVE'})
 def test_complete(self): self.assertEqual(expected_for('damage',[dict(D[0],decoded=1800)]),{'D1':'REVIEW'})
 def test_bool(self): self.assertEqual(validate('damage',[dict(D[0],decoded=True)],'{}')['reason'],'INVALID_TASK')
 def test_overcount(self): self.assertEqual(validate('damage',[dict(D[0],decoded=1801)],'{}')['reason'],'INVALID_TASK')
 def test_duplicate_input(self): self.assertEqual(validate('damage',D+D,'{}')['reason'],'INVALID_TASK')
 def test_duplicate_output(self): self.assertEqual(validate('damage',D,'{"D1":"REVIEW","D1":"PRESERVE"}')['reason'],'INVALID_JSON')
 def test_missing(self): self.assertEqual(validate('damage',D,'{}')['reason'],'SCHEMA_MISMATCH')
 def test_extra(self): self.assertEqual(validate('damage',D,'{"D1":"PRESERVE","D2":"PRESERVE"}')['reason'],'SCHEMA_MISMATCH')
 def test_code_fence(self): self.assertEqual(validate('damage',D,'```json\n{}\n```')['reason'],'INVALID_JSON')
 def test_array(self): self.assertEqual(validate('damage',D,'[]')['reason'],'SCHEMA_MISMATCH')
 def test_nonstring(self): self.assertEqual(validate('damage',D,'{"D1":true}')['reason'],'SCHEMA_MISMATCH')
 def test_pending(self): self.assertTrue(validate('pending',S,'{"S1":"QUEUED"}')['accepted'])
 def test_no_promotion(self): self.assertEqual(validate('pending',S,'{"S1":"VERIFIED"}')['reason'],'SEMANTIC_MISMATCH')
 def test_unknown_gate(self): self.assertEqual(validate('pending',S,'{"S1":"QUEUED"}',False)['reason'],'INVALID_TASK')
 def test_no_release(self): self.assertFalse(validate('pending',S,'{"S1":"QUEUED"}')['release_allowed'])
 def test_size_cap(self): self.assertEqual(validate('damage',D,' '*16385)['reason'],'INVALID_RESPONSE')
class EnvelopeTests(unittest.TestCase):
 def envelope(self,content='{"D1":"PRESERVE"}',finish='stop',**message): return {'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':content,**message}}]}
 def test_good(self): self.assertTrue(evaluate_recovery_response('damage',D,self.envelope())['accepted'])
 def test_length(self): self.assertFalse(evaluate_recovery_response('damage',D,self.envelope(finish='length'))['accepted'])
 def test_tools(self): self.assertFalse(evaluate_recovery_response('damage',D,self.envelope(tool_calls=[{}]))['accepted'])
 def test_wrong_role(self): self.assertFalse(evaluate_recovery_response('damage',D,self.envelope(role='user'))['accepted'])
 def test_multiple(self): self.assertFalse(evaluate_recovery_response('damage',D,{'choices':self.envelope()['choices']*2})['accepted'])
 def test_missing(self): self.assertFalse(evaluate_recovery_response('damage',D,{})['accepted'])
 def test_semantic(self): self.assertEqual(evaluate_recovery_response('damage',D,self.envelope('{"D1":"REVIEW"}'))['reason'],'SEMANTIC_MISMATCH')
 def test_invalid_input_release(self): self.assertIs(validate('damage',[], '{}')['release_allowed'],False)
 def test_deep_json(self):
  result=validate('damage',D,'['*2000+']'*2000); self.assertFalse(result['accepted']); self.assertFalse(result['release_allowed']); self.assertIn(result['reason'],('INVALID_JSON','SCHEMA_MISMATCH'))
 def test_input_bound(self): self.assertEqual(validate('damage',D*257,'{}')['reason'],'INVALID_TASK')
class LocalModelRegressionTests(unittest.TestCase):
 def test_verified_negative_cases(self):
  import json
  from pathlib import Path
  cases=json.loads(Path(__file__).with_name('recovery_acceptance_local_cases.json').read_text())
  for content,reason in cases.items():
   with self.subTest(content=content):
    r=validate('damage',D,content);self.assertFalse(r['accepted']);self.assertFalse(r['release_allowed']);self.assertEqual(r['reason'],reason)
if __name__=='__main__':unittest.main()
