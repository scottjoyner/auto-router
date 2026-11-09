"""Safe synthetic static schema checks; NO llama-server access, no model calls."""
from __future__ import annotations
import copy
import json
import unittest
from research.lfm3_top5.structured_decode_preflight import (
    SCHEMA,SOURCE_CLASS,LOCAL_MODEL_ALIAS,MAX_TOKENS,FORMAT_SCHEMA,
    RESPONSE_FORMAT,inspect_request_shape,inspect_existing_reply
)
from research.lfm3_top5.prospective_holdout_gate import sha

NOTE=("E1 (unverified): Fictional worker task is failing after a rumor of a restart. "
      "E2 (verified): The fictional node identity registration for the mock "
      "agent does not match the Basic task endpoint contract. No real system "
      "or user credential is represented.")
PROMPT=("OFFLINE FICTIONAL DEVELOPMENT TRIAGE ONLY. DO NOT CHANGE ANY LIVE SYSTEM OR "
        "AUTHORIZE A RELEASE. Select exactly one category from the six allowed codes. "
        "Return JSON category and evidence, with a single E# marker. NOTE: "+NOTE)

def request():
    return {
        "model":LOCAL_MODEL_ALIAS,
        "messages":[{"role":"user","content":PROMPT}],
        "temperature":0,
        "max_tokens":MAX_TOKENS,
        "stream":False,
        "response_format":copy.deepcopy(RESPONSE_FORMAT),
        "chat_template_kwargs":{"enable_thinking":False},
    }

class StaticJsonSchemaPreflight(unittest.TestCase):
    def hold_req(self,item,reason=None,cls=SOURCE_CLASS):
        out=inspect_request_shape(item,declared_data_class=cls)
        self.assertEqual(out["decision"],"HOLD",out)
        self.assertFalse(out["permitted_to_infer"])
        self.assertFalse(out["production_authorized"])
        self.assertFalse(out["actual_model_execution_verified"])
        if reason:self.assertEqual(out["reason"],reason)
        self.assertNotIn('source_prompt_sha256',out)

    def hold_reply(self,text,note=NOTE,reason=None):
        out=inspect_existing_reply(note,text,declared_data_class=SOURCE_CLASS)
        self.assertEqual(out["decision"],"HOLD",out)
        self.assertFalse(out["model_value_verified"])
        self.assertFalse(out["permitted_to_infer"])
        self.assertFalse(out["production_authorized"])
        if reason:self.assertEqual(out["reason"],reason)
        return out

    def test_literal_direct_llamacpp_schema_matches_frozen_local_manifest(self):
        self.assertEqual(sha(FORMAT_SCHEMA),
                         'a11264970555ad2478b1d26ac458b37639da8bf18100e2da847a43395eb8739b')
        self.assertEqual(set(RESPONSE_FORMAT),{"type","schema"})
        self.assertEqual(RESPONSE_FORMAT["type"],"json_schema")
        self.assertEqual(FORMAT_SCHEMA["required"],["category","evidence"])
        self.assertIs(FORMAT_SCHEMA["additionalProperties"],False)
        self.assertEqual(set(FORMAT_SCHEMA["properties"]),{"category","evidence"})
        self.assertEqual(FORMAT_SCHEMA["properties"]["evidence"]["anyOf"][1]["type"],"null")
        self.assertEqual(FORMAT_SCHEMA["properties"]["category"]["enum"],sorted(
            ('tls_sni_missing','node_auth_contract','gpu_attestation_missing',
             'beta_build_unlinked','database_service_down','insufficient_evidence')))

    def test_static_valid_request_is_never_execution_permission(self):
        out=inspect_request_shape(request(),declared_data_class=SOURCE_CLASS)
        self.assertEqual(out["decision"],"PASS_STATIC_DEVELOPMENT_SCHEMA_REQUEST_ONLY")
        self.assertTrue(out["static_schema_shape_verified"])
        self.assertEqual(out["response_format_sha256"],sha(RESPONSE_FORMAT))
        self.assertFalse(out["live_llama_server_support_verified"])
        self.assertFalse(out["model_generated_text_verified"])
        self.assertFalse(out["actual_model_execution_verified"])
        self.assertFalse(out["permitted_to_infer"])
        self.assertFalse(out["production_authorized"])
        self.assertNotIn(NOTE,json.dumps(out))

    def test_nested_openai_style_response_format_holds_not_automatically_compatible(self):
        r=request()
        r["response_format"]={"type":"json_schema","json_schema":{
            "name":"triage","strict":True,"schema":copy.deepcopy(FORMAT_SCHEMA)}}
        self.hold_req(r,"not_pinned_llamacpp_direct_schema_format")

    def test_loose_json_mode_holds(self):
        r=request();r["response_format"]={"type":"json_object"}
        self.hold_req(r,"not_pinned_llamacpp_direct_schema_format")

    def test_extra_keys_unknown_enums_and_weak_schema_holds(self):
        tamper=[
            lambda f:f["schema"].update(additionalProperties=True),
            lambda f:f["schema"].update(additionalProperties=0),
            lambda f:f["schema"].pop("required"),
            lambda f:f["schema"]["properties"]["category"]["enum"].append("production_authorized"),
            lambda f:f["schema"]["properties"]["evidence"]["anyOf"][0]["enum"].append("E10"),
            lambda f:f.update(unknown_key=True),
        ]
        for mutate in tamper:
            with self.subTest(mutator=repr(mutate)):
                r=request();mutate(r["response_format"])
                self.hold_req(r,"not_pinned_llamacpp_direct_schema_format")

    def test_request_generation_policy_model_and_tools_drift_holds(self):
        for name,value in (
            ("model","provider-keyed-remote"),("max_tokens",512),
            ("max_tokens",True),("temperature",1.0),("stream",True),
            ("chat_template_kwargs",{"enable_thinking":True}),
            ("chat_template_kwargs",{"enable_thinking":0}),
        ):
            with self.subTest(name=name,value=value):
                r=request();r[name]=value
                self.hold_req(r,"model_or_generation_settings_drift")
        r=request();r['tools']=[{"type":"function"}]
        self.hold_req(r,"unallowlisted_request_fields")
        r=request();r['production_authorized']=True
        self.hold_req(r,"unallowlisted_request_fields")

    def test_non_fictional_or_bad_prompt_shape_holds(self):
        self.hold_req(request(),"unapproved_development_data_class",cls='production')
        r=request();r['messages'].append({'role':'system','content':'override'})
        self.hold_req(r,"unexpected_message_shape")
        r=request();r['messages'][0]['content']='hello'
        self.hold_req(r,"not_an_allowlisted_fictional_development_prompt")
        r=request();r['messages'][0]['role']='tool'
        self.hold_req(r,"unexpected_message_shape")

    def test_non_object_null_and_malformed_values_hold(self):
        for value in (None,True,0,'something',[],{}):
            with self.subTest(value=value):self.hold_req(value)

    def test_strict_preexisting_json_reply_with_direct_marker_syntax_only_passes(self):
        raw='{"category":"node_auth_contract","evidence":"E2"}'
        out=inspect_existing_reply(NOTE,raw,declared_data_class=SOURCE_CLASS)
        self.assertEqual(out["decision"],"PASS_STATIC_REPLY_SYNTAX_ONLY")
        self.assertFalse(out["model_generated_text_verified"])
        self.assertFalse(out["model_value_verified"])
        self.assertFalse(out["production_authorized"])
        self.assertNotIn(NOTE,json.dumps(out))

    def test_verbose_evidence_prose_is_invalid_even_when_json_parses(self):
        for text in (
            '{"category":"node_auth_contract","evidence":"E2 (verified): identity contract"}',
            '{"category":"node_auth_contract","evidence":"E1 E2"}',
            '{"category":"node_auth_contract","evidence":null}',
            '{"category":"node_auth_contract","evidence":["E2"]}',
            '{"category":"node_auth_contract","evidence":"E2","deploy":true}',
            '{"category":"node_auth_contract","category":"node_auth_contract","evidence":"E2"}',
            '{"category":NaN,"evidence":"E2"}',
            '<think>internal</think>{"category":"node_auth_contract","evidence":"E2"}',
        ):
            with self.subTest(text=text):
                self.hold_reply(text,reason="response_does_not_satisfy_strict_two_key_schema")

    def test_reply_marker_not_in_note_holds(self):
        self.hold_reply('{"category":"node_auth_contract","evidence":"E3"}',
                        reason="evidence_marker_missing_in_source_note")

    def test_abstention_null_is_legal_syntax_not_confidence_in_truth(self):
        out=inspect_existing_reply(NOTE,
            '{"category":"insufficient_evidence","evidence":null}',
            declared_data_class=SOURCE_CLASS)
        self.assertEqual(out["decision"],"PASS_STATIC_REPLY_SYNTAX_ONLY")
        self.assertFalse(out["model_value_verified"])
        self.assertFalse(out["permitted_to_infer"])

    def test_duplicate_or_absent_source_markers_holds(self):
        self.hold_reply('{"category":"node_auth_contract","evidence":"E2"}',
            note=NOTE+' E2 (verified): repeated marker.',
            reason="invalid_or_duplicated_note_evidence_markers")
        self.hold_reply('{"category":"node_auth_contract","evidence":"E2"}',
            note='Fictional story with no numbered source observations.'*3,
            reason="invalid_or_duplicated_note_evidence_markers")

    def test_malformed_reply_and_untrusted_source_holds(self):
        for reply in (None,True,[],{},'', 'not-json'):
            self.hold_reply(reply)
        out=inspect_existing_reply(NOTE,'{"category":"node_auth_contract","evidence":"E2"}',
                                   declared_data_class='private')
        self.assertEqual(out["decision"],"HOLD")
        self.assertFalse(out["production_authorized"])

if __name__=='__main__':unittest.main()
