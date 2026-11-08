"""Pure fail-closed audit of locally preregistered synthetic narrative triage.

Source notes/oracles are intentionally public fictional text. This module
never executes inference, changes fleet state, authorizes releases, or
claims statistically generalizable quality. Local raw execution receipts
are unsigned operator observations, not independent GPU kernel attestation.
"""
from __future__ import annotations
import hashlib,json,math,random,re
from statistics import median

MODEL_SHA="43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452"
SCRIPT_SHA="457c3a8d67c5a1401555f12ba4fd1d9720b29066e2f18985b7b8f643fe60c6dc"
FIXTURE_SHA="09cfab8ce640a56c8ffb4977d5b2583c48913b63f03ee3de8dafd74b55e2a074"
SCENARIOS=("database","gpu_witness","node_auth","pilot","sni","unknown")
SOURCE_CASE_ORDER=("sni","node_auth","gpu_witness","pilot","database","unknown")
ORDER=(("cpu",1),("vulkan8",1),("vulkan8",2),("cpu",2))
CATEGORIES=frozenset(("beta_build_unlinked","database_service_down","gpu_attestation_missing",
                      "insufficient_evidence","node_auth_contract","tls_sni_missing"))

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True)

def hold(reason):
    return {
      "decision":"HOLD","reason":reason,
      "blocks_verified":0,"requests_verified":0,"correct_model_answers":None,
      "source_custody_independently_signed":False,
      "gpu_execution_independently_attested":False,
      "quality_generalizable":False,
      "production_authorized":False,
    }

def _pairs_no_duplicate(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError("duplicate_json_key")
        out[k]=v
    return out

def _invalid_constant(_):
    raise ValueError("non_finite_json_constant")

def exact_answer(answer,ground):
    if type(answer) is not str or not 1<=len(answer)<=2000 or "<think>" in answer.lower():
        return False
    if type(ground) is not dict:return False
    try:
        obj=json.loads(answer,object_pairs_hook=_pairs_no_duplicate,
                       parse_constant=_invalid_constant)
    except (TypeError,ValueError,RecursionError):
        return False
    return type(obj) is dict and canonical(obj)==canonical(ground)

def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()

def _valid_capacity(m):
    if type(m) is not dict:return False
    for k,v in (("MemAvailable_KiB",10*1024*1024),("SwapFree_KiB",1024*1024)):
        if type(m.get(k)) is not int or m[k]<v:return False
    for k,mx in (("memory_psi_avg10",1),("cpu_psi_avg10",2),("io_psi_avg10",5)):
        v=m.get(k)
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=mx:return False
    gpu=m.get("gpu_busy_percent");used=m.get("mem_info_vram_used");total=m.get("mem_info_vram_total")
    return (type(gpu) is int and 0<=gpu<=10 and
       type(used) is int and type(total) is int and
       0<=used<=total and total-used>=1536*1024*1024)

def _fixture_ok(fixture):
    if type(fixture) is not dict or fixture.get("schema")!="lfm3.unstructured.synthetic.v1":
        return False
    if type(fixture.get("seed")) is not int or fixture["seed"]!=202611081646:
        return False
    tasks=fixture.get("tasks")
    if type(tasks) is not dict or set(tasks)!=set(SCENARIOS):return False
    for name,task in tasks.items():
        if type(task) is not dict or set(task)!={"source_note","prompt","expected"}:return False
        if type(task["source_note"]) is not str or not 100<=len(task["source_note"])<=1200:return False
        if type(task["prompt"]) is not str or not 100<=len(task["prompt"])<=2000:return False
        ground=task["expected"]
        if (type(ground) is not dict or set(ground)!={"category","evidence"} or
            type(ground["category"]) is not str or ground["category"] not in CATEGORIES or
            ground["evidence"]!="E3"):return False
        if task["source_note"] not in task["prompt"]:return False
    expected_file=json.dumps(fixture,indent=2,sort_keys=True)+"\n"
    return _sha(expected_file)==FIXTURE_SHA

def _stage_order(block,fixture):
    # SOURCE_CASE_ORDER is the pinned authored insertion order of the
    # remote loader's Python TASKS literal. JSON fixture key sorting
    # is only for canonical hashing and must not change inference order.
    x=list(SOURCE_CASE_ORDER)
    random.Random(fixture["seed"]+block).shuffle(x)
    return x

def _quality_stage(receipt,mode,block,fixture):
    if type(receipt) is not dict:return hold("missing_stage_receipt")
    layer={"cpu":0,"vulkan8":8}[mode]
    sequence=_stage_order(block,fixture)
    must={
      "schema":"lfm3.deathstar.unstructured-triage-block-v1",
      "host":"deathstar-XPS-8920",
      "mode":mode,"block":block,"workload":"six_public_cases",
      "model_basename":"LFM3-1.7B-QAD-Q4_0.gguf",
      "model_sha256_expected":MODEL_SHA,"model_sha256_observed":MODEL_SHA,
      "device":"none" if layer==0 else "Vulkan0",
      "offloaded_layers_requested":layer,
      "port":"127.0.0.1:19191","alias":"lfm3-deathstar-unstructured-research",
      "requests_requested":6,"requests_attempted":6,"max_tokens":96,
      "case_order":sequence,"outcome":"PASS_RESEARCH_CAPTURE_WITH_QUALITY_SCORES",
      "production_authorized":False,"independent_tensor_execution_attested":False,
      "cpu_mode_gpu_compute_counter_verified":False,
      "credentials_read":False,"existing_services_modified":False,
      "public_ingress_enabled":False,
      "temporary_child_stopped":True,"temporary_log_removed":True,
      "temporary_port_closed":True,
    }
    for k,v in must.items():
        if type(receipt.get(k)) is not type(v) or receipt[k]!=v:
            return hold("identity_or_safety_"+k)
    for k in ("preflight","post_hash","capacity_after_cleanup"):
        if not _valid_capacity(receipt.get(k)):return hold("unsafe_capacity_"+k)
    cool=receipt.get("cooldown")
    if type(cool) is not list or not 3<=len(cool)<=18:return hold("missing_load_cooldown")
    for c in cool:
        if type(c) is not dict:return hold("malformed_cooldown")
        p=c.get("memory_psi");g=c.get("gpu_busy")
        if (type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<5 or
            type(g) is not int or not 0<=g<=15):
            return hold("unsafe_cooldown")
    if any(c["memory_psi"]>1 or c["gpu_busy"]>10 for c in cool[-3:]):
        return hold("postload_pressure_not_quiescent")
    off=receipt.get("runtime_offload")
    if type(off) is not dict:return hold("loader_not_observed")
    if layer:
        if (off.get("summaries")!=[[8,25],[8,25]] or
            off.get("assigned_layers")!=list(range(17,25))*2):
            return hold("gpu_offload_mismatch")
        v=off.get("final_vulkan_model_buffer_mib")
        if type(v) not in (float,int) or not math.isfinite(v) or not 100<v<900:
            return hold("gpu_model_buffer_missing")
    elif off.get("assigned_layers")!=[] or off.get("summaries") not in (
        [],[[0,25]],[[0,25],[0,25]]):
        return hold("unexpected_cpu_gpu_offload")
    rows=receipt.get("requests")
    if type(rows) is not list or len(rows)!=6:return hold("six_complete_responses_missing")
    if [r.get("case_id") if type(r) is dict else None for r in rows]!=sequence:
        return hold("case_sequence_reordered")
    ids=set();process=set();clients=set();correct=0;cases=[]
    for row in rows:
        case_id=row["case_id"];expected=fixture["tasks"][case_id]
        rid=row.get("id")
        if type(rid) is not str or not rid or len(rid)>128 or any(ch.isspace() for ch in rid):
            return hold("malformed_request_id")
        if rid in ids:return hold("duplicate_request_id")
        ids.add(rid)
        if (row.get("case_prompt_sha256")!=_sha(expected["prompt"]) or
            row.get("case_expected_sha256")!=_sha(canonical(expected["expected"]))):
            return hold("case_prompt_or_oracle_drift")
        if row.get("finish_reason") not in ("stop","length"):return hold("bad_termination")
        ans=row.get("full_public_synthetic_answer")
        if (type(ans) is not str or _sha(ans)!=row.get("response_sha256") or
            row.get("response_first_100_chars")!=ans[:100]):
            return hold("response_integrity_mismatch")
        good=exact_answer(ans,expected["expected"])
        if row.get("semantic_oracle_pass") is not good:return hold("self_reported_quality_mismatch")
        correct+=int(good)
        usage=row.get("usage");tim=row.get("timings")
        if type(usage) is not dict or type(tim) is not dict:return hold("missing_token_metrics")
        n=usage.get("completion_tokens");p=usage.get("prompt_tokens")
        if (type(n) is not int or not 2<=n<=96 or type(p) is not int or not 10<=p<=2048 or
            usage.get("total_tokens")!=n+p or tim.get("predicted_n")!=n):
            return hold("token_count_mismatch")
        for k in ("predicted_per_second","predicted_ms","prompt_ms","prompt_per_second"):
            value=tim.get(k)
            if type(value) not in (float,int) or not math.isfinite(value) or not 0<value<100000:
                return hold("nonfinite_or_missing_timings")
        calculated=(n-1)*1000/tim["predicted_ms"]
        if not math.isclose(calculated,tim["predicted_per_second"],rel_tol=.01):
            return hold("native_decode_speed_inconsistent")
        http=row.get("http_wall_ms");full=row.get("http_plus_validation_wall_ms")
        validation=row.get("validation_wall_ms")
        if any(type(t) not in (float,int) or not math.isfinite(t) for t in (http,full,validation)):
            return hold("bad_measurement")
        if not 0<http<=22000 or not http<=full<=http+300 or not 0<validation<=1000:
            return hold("invalid_request_time_bounds")
        if tim["predicted_ms"]>http*1.02:return hold("decode_time_exceeds_http")
        pid=row.get("pid");tick=row.get("process_start_ticks")
        if type(pid) is not int or pid<=0 or type(tick) is not int or tick<=0:
            return hold("missing_process_identity")
        process.add((pid,tick))
        for k in ("pre_pressure","post_pressure"):
            v=row.get(k)
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:
                return hold("unsafe_pressure")
        v=row.get("post_vram_free_mib")
        if type(v) not in (int,float) or not math.isfinite(v) or v<1536:
            return hold("vram_headroom_too_low")
        gpu=row.get("drm_compute_delta_ns")
        if layer:
            if type(gpu) is not int or gpu<=0:return hold("no_gpu_process_activity")
            cid=row.get("drm_client")
            if type(cid) is not str or not cid.isdigit():return hold("gpu_client_missing")
            clients.add(cid)
        elif gpu not in (None,0):
            return hold("cpu_gpu_compute_detected")
        cases.append({"case_id":case_id,"exact_correct":good,"http_plus_validation_ms":full,
                      "http_wall_ms":http,"validation_ms":validation})
    if len(process)!=1 or (layer and len(clients)!=1):
        return hold("process_or_gpu_client_changed")
    if type(receipt.get("oracle_pass_count")) is not int or receipt["oracle_pass_count"]!=correct:
        return hold("reported_aggregate_grade_mismatch")
    return {"decision":"PASS_SAFE_UNSTRUCTURED_CAPTURE_RESEARCH_ONLY",
      "mode":mode,"block":block,"cases":cases,"exact_correct":correct,"requests":6,
      "production_authorized":False}

def audit_unstructured(prereg,fixture,receipts):
    if type(prereg) is not dict or type(receipts) is not dict or not _fixture_ok(fixture):
        return hold("prereg_fixture_or_receipts_untrusted")
    must={
       "schema":"lfm3.deathstar.unstructured-triage-preregistered.v1",
       "host":"deathstar-XPS-8920","study_date":"2026-11-08",
       "primary_order":[{"mode":m,"block":b} for m,b in ORDER],
       "task_ids":list(SCENARIOS),
       "script_sha256":SCRIPT_SHA,"fixture_sha256":FIXTURE_SHA,
       "checkpoint_sha256":MODEL_SHA,
       "exploratory_cpu_block_excluded":True,"maximum_requests":24,
       "independent_gpu_execution_attestation":False,
       "production_authorized":False,
    }
    for k,v in must.items():
        if type(prereg.get(k)) is not type(v) or prereg[k]!=v:
            return hold("preregistered_"+k+"_mismatch")
    if set(receipts)!=set(ORDER):return hold("missing_or_extra_stages")
    output={};seen_ids=set();seen_process=set()
    for mode,block in ORDER:
        receipt=receipts[(mode,block)]
        a=_quality_stage(receipt,mode,block,fixture)
        if a["decision"]!="PASS_SAFE_UNSTRUCTURED_CAPTURE_RESEARCH_ONLY":
            return hold("stage_"+mode+str(block)+"_"+a["reason"])
        ident=(receipt["requests"][0]["pid"],receipt["requests"][0]["process_start_ticks"])
        if ident in seen_process:return hold("process_reused_across_stages")
        seen_process.add(ident)
        for row in receipt["requests"]:
            if row["id"] in seen_ids:return hold("request_id_repeated_across_stages")
            seen_ids.add(row["id"])
        output[(mode,block)]=a
    stats={}
    for mode in ("cpu","vulkan8"):
        cases=[item for block in (1,2) for item in output[(mode,block)]["cases"]]
        if len(cases)!=12:return hold("missing_mode_cases")
        by_id={id:[x for x in cases if x["case_id"]==id] for id in SCENARIOS}
        if any(len(v)!=2 for v in by_id.values()):return hold("case_replication_mismatch")
        measured=[x["http_plus_validation_ms"] for x in cases]
        total=sum(measured)
        correct=sum(x["exact_correct"] for x in cases)
        stats[mode]={
            "responses":12,"distinct_synthetic_scenarios":6,
            "correct_exact":correct,"accuracy_percent":round(100*correct/12,2),
            "median_http_plus_local_validation_ms":round(median(measured),3),
            "sum_http_plus_local_validation_ms":round(total,3),
            "verified_correct_per_observed_request_second":round(correct/(total/1000),6),
            "by_scenario":{key:{"correct":sum(x["exact_correct"] for x in rows),
                 "total":2} for key,rows in by_id.items()},
        }
    return {
        "decision":"PASS_BOUNDED_UNSTRUCTURED_RESEARCH_ONLY",
        "verified_stages":4,"verified_requests":24,
        "distinct_synthetic_scenarios":6,
        "model_modes":stats,
        "measurement":"same_host_same_request_http_plus_local_response_validation",
        "included_in_measured_request_time":"model_http_plus_response_integrity_checks_and_json_oracle",
        "excluded_from_measured_request_time":["model_startup","full_checkpoint_hash",
          "source_custody_verification","network_beyond_loopback","energy","queue"],
        "task_routing_policy_verified":False,
        "external_source_custody_signed":False,
        "independent_gpu_tensor_execution_attested":False,
        "cpu_zero_gpu_activity_independently_verified":False,
        "provider_billing_verified_free":False,
        "statistical_generalization_established":False,
        "production_authorized":False,
    }
