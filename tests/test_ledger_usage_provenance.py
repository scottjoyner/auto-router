"""Historical quota estimates are never promoted to measured inference tokens."""
import sqlite3

import pytest

from auto_router.ledger import RuntimeSample, UsageEvent, UsageLedger


def _event(request_id, source, prompt, output, total, status=200):
    return UsageEvent(
        request_id=request_id,
        provider_id="local",
        model_id="model",
        route="chat_completions",
        priority="interactive",
        stage="final",
        input_tokens=prompt,
        output_tokens=output,
        total_tokens=total,
        token_usage_source=source,
        status_code=status,
    )


def _sample(request_id, source, output, status=200):
    return RuntimeSample(
        request_id=request_id,
        provider_id="local",
        model_id="model",
        route="chat_completions",
        priority="interactive",
        stage="final",
        output_tokens=output,
        total_tokens=output,
        token_usage_source=source,
        status_code=status,
    )


def test_new_usage_rows_distinguish_measured_from_unknown(tmp_path):
    ledger=UsageLedger(f"sqlite:///{tmp_path/'db.sqlite3'}")
    ledger.record(_event("measured", "provider_reported", 18, 6, 24))
    ledger.record(_event("failed", "unknown", 0, 0, 0, status=503))
    ledger.record(_event("zero", "provider_reported", 0, 0, 0))
    ledger.record_runtime_sample(_sample("measured", "provider_reported", 6))
    ledger.record_runtime_sample(_sample("failed", "unknown", 0, status=503))
    rows=ledger.recent_events()
    assert {x["request_id"]:x["token_usage_source"] for x in rows} == {
        "measured":"provider_reported",
        "failed":"unknown",
        "zero":"provider_reported",
    }
    totals=ledger.summary()["totals"]
    assert totals["requests"] == 3
    assert totals["measured_requests"] == 2
    assert totals["unknown_usage_requests"] == 1
    assert totals["legacy_unverified_requests"] == 0
    assert (totals["input_tokens"],totals["output_tokens"],totals["total_tokens"])==(18,6,24)
    summary=ledger.summary()
    assert summary["window"]=="retained_sqlite_rows_not_fleet_24h"
    assert summary["by_provider"][0]["tokens"]==24
    samples=ledger.recent_runtime_samples()
    assert {x["request_id"]:x["token_usage_source"] for x in samples} == {
        "measured":"provider_reported",
        "failed":"unknown",
    }


def test_old_schema_is_migrated_without_relabeling_or_rewriting_token_amounts(tmp_path):
    path=tmp_path/"old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                provider_id TEXT, model_id TEXT, route TEXT NOT NULL,
                priority TEXT NOT NULL, stage TEXT,
                input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0, quota_units_json TEXT DEFAULT '{}',
                status_code INTEGER, latency_ms INTEGER, error_type TEXT,
                error_message TEXT, created_at INTEGER NOT NULL
            );
            CREATE TABLE runtime_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                provider_id TEXT, model_id TEXT, route TEXT NOT NULL,
                priority TEXT NOT NULL, stage TEXT, started_at_ms INTEGER,
                ended_at_ms INTEGER, queue_wait_ms INTEGER,
                load_time_ms INTEGER, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
                tokens_per_second REAL, value_units INTEGER DEFAULT 0,
                value_per_second REAL, status_code INTEGER, latency_ms INTEGER,
                error_type TEXT, error_message TEXT, created_at INTEGER NOT NULL
            );
            INSERT INTO usage_events (
                request_id, provider_id, model_id,route,priority,stage,
                input_tokens,output_tokens,total_tokens,status_code,created_at
            ) VALUES ('old-quota','local','model','embeddings','background','final',
                     200000,1200,201200,503,1);
            INSERT INTO runtime_samples (
                request_id,provider_id,model_id,route,priority,stage,
                input_tokens,output_tokens,total_tokens,tokens_per_second,
                value_units,value_per_second,status_code,created_at
            ) VALUES ('old-quota','local','model','embeddings','background','final',
                     200000,1200,201200,50000,1200,50000,503,1);
        """)
    ledger=UsageLedger(f"sqlite:///{path}")
    summary=ledger.summary()
    assert summary["totals"]["requests"]==1
    assert summary["totals"]["legacy_unverified_requests"]==1
    assert summary["totals"]["total_tokens"]==0
    assert summary["by_provider"][0]["tokens"]==0
    assert ledger.recent_events()[0]["total_tokens"]==201200
    assert ledger.recent_events()[0]["token_usage_source"]=="legacy_unverified"
    assert ledger.recent_runtime_samples()[0]["token_usage_source"]=="legacy_unverified"
    assert ledger.runtime_summary()["avg_tokens_per_second"] is None
    assert ledger._candidate_prediction(
        {"provider":"local","provider_model":"model"}
    )["predicted_value_per_hour"] is None
    # Idempotent initialization cannot silently promote legacy data.
    again=UsageLedger(f"sqlite:///{path}")
    assert again.summary()["totals"]["legacy_unverified_requests"]==1
    again.record(_event("new-real", "provider_reported", 9, 3, 12))
    assert again.summary()["totals"]["total_tokens"]==12
    assert again.summary()["totals"]["legacy_unverified_requests"]==1


def test_untrusted_provenance_is_rejected_at_write(tmp_path):
    ledger=UsageLedger(f"sqlite:///{tmp_path/'db.sqlite3'}")
    with pytest.raises(ValueError, match="invalid token usage provenance"):
        ledger.record(_event("fake", "legacy_unverified", 20, 20, 40))
    with pytest.raises(ValueError, match="invalid token usage provenance"):
        ledger.record_runtime_sample(_sample("fake", "fake_measured", 20))
    assert ledger.summary()["totals"]["requests"]==0


def test_provider_reported_usage_retained_even_on_failed_status(tmp_path):
    ledger=UsageLedger(f"sqlite:///{tmp_path/'db.sqlite3'}")
    ledger.record(_event("failed-provider-reported", "provider_reported", 4, 2, 6, status=503))
    totals=ledger.summary()["totals"]
    assert totals["total_tokens"]==6
    assert totals["measured_requests"]==1
    assert ledger.recent_events()[0]["status_code"]==503
    # A provider can report consumed tokens for a failed routed request;
    # success-only generation must be reported separately if needed.



def test_old_throughput_and_prediction_are_not_promoted(tmp_path):
    db=tmp_path/"db.sqlite3"
    ledger=UsageLedger(f"sqlite:///{db}")
    ledger.record_runtime_sample(RuntimeSample(
        request_id="old-estimate",provider_id="p",model_id="m",
        route="chat_completions",priority="interactive",stage="final",
        token_usage_source="unknown",tokens_per_second=50000.0,
        value_units=100000,value_per_second=50000.0,status_code=200,
    ))
    summary=ledger.runtime_summary()
    assert summary["samples"]==1
    assert summary["verified_throughput_samples"]==0
    assert summary["avg_tokens_per_second"] is None
    assert summary["avg_value_per_second"] is None
    candidate=ledger._candidate_prediction({"provider":"p","provider_model":"m"})
    assert candidate["predicted_value_per_hour"] is None
    ledger.record_runtime_sample(RuntimeSample(
        request_id="measured",provider_id="p",model_id="m",
        route="chat_completions",priority="interactive",stage="final",
        token_usage_source="provider_reported",tokens_per_second=18.0,
        value_units=9,value_per_second=18.0,status_code=200,
    ))
    summary=ledger.runtime_summary()
    assert summary["avg_tokens_per_second"]==18.0
    assert summary["verified_throughput_samples"]==1
    candidate=ledger._candidate_prediction({"provider":"p","provider_model":"m"})
    assert candidate["predicted_value_per_hour"]==64800.0


def test_counterfactual_requires_measured_successful_outcome(tmp_path):
    ledger=UsageLedger(f"sqlite:///{tmp_path/'db.sqlite3'}")
    ledger.record_counterfactual_decision(
        decision_id="d1",request_id="r1",stage="final",
        chosen={"provider":"p","provider_model":"m"},
        candidates=[{"provider":"p","provider_model":"m"}],
        rejections=[],
    )
    ledger.record_runtime_sample(RuntimeSample(
        request_id="r1",provider_id="p",model_id="m",route="chat_completions",
        priority="interactive",stage="final",value_per_second=100000.0,
        token_usage_source="unknown",status_code=200,
    ))
    item=ledger.counterfactual_summary()["items"][0]
    assert item["status"]=="completed"
    assert item["realized_value_per_hour"] is None
    assert item["realized_regret"] is None
