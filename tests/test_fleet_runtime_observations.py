from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_router import fleet_routes


def test_runtime_observation_sanitizer_forces_non_admitting_boundary() -> None:
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:k2",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b", "k2-36b"],
                "ready": True,
                "observed_at": 123,
                "admitted": True,
                "artifact_fingerprint": "must-not-cross-observation-boundary",
                "router_token": "must-not-cross-observation-boundary",
            }
        ]
    )

    assert observations == [
        {
            "observation_schema": "fleet-runtime-observation.v1",
            "runtime_observation_id": "runtime-observation:k2",
            "runtime_kind": "openai_compatible",
            "protocol": "openai-compatible",
            "base_url": "http://destroyer:1235",
            "models": ["k2-36b"],
            "ready": True,
            "observed_model_count": 1,
            "models_truncated": False,
            "observed_at": 123,
            "admitted": False,
        }
    ]
    serialized = repr(observations)
    assert "artifact_fingerprint" not in serialized
    assert "router_token" not in serialized


def test_runtime_observation_rejects_credentialed_or_unknown_endpoints() -> None:
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:credentialed",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://user:password@destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
            },
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:unknown-kind",
                "runtime_kind": "mystery-runtime",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
            },
        ]
    )

    assert observations == []


def test_node_report_preserves_runtime_evidence_without_admitting_it() -> None:
    fleet_routes._node_reports.clear()
    app = FastAPI()
    app.include_router(fleet_routes.router)
    client = TestClient(app)

    response = client.post(
        "/api/fleet/node-report",
        json={
            "hostname": "destroyer",
            "library": [],
            "loaded": [],
            "runtimes": [
                {
                    "observation_schema": "fleet-runtime-observation.v1",
                    "runtime_observation_id": "runtime-observation:k2",
                    "runtime_kind": "openai_compatible",
                    "protocol": "openai-compatible",
                    "base_url": "http://destroyer:1235",
                    "models": ["k2-36b"],
                    "ready": True,
                    "observed_at": 123,
                    "admitted": True,
                },
                {
                    "observation_schema": "fleet-runtime-observation.v1",
                    "runtime_observation_id": "runtime-observation:bonsai",
                    "runtime_kind": "openai_compatible",
                    "protocol": "openai-compatible",
                    "base_url": "http://destroyer:38898",
                    "models": ["ternary-bonsai-2"],
                    "ready": True,
                    "observed_at": 123,
                },
            ],
        },
    )

    assert response.status_code == 200
    stored = fleet_routes._node_reports["destroyer"]
    assert len(stored["runtimes"]) == 2
    assert stored["runtime_observations_truncated"] is False
    assert stored["source_ip"] == "testclient"
    assert all(runtime["admitted"] is False for runtime in stored["runtimes"])
    assert all(runtime["observed_model_count"] == 1 for runtime in stored["runtimes"])
    assert {
        model
        for runtime in stored["runtimes"]
        for model in runtime["models"]
    } == {"k2-36b", "ternary-bonsai-2"}


def test_empty_models_cannot_claim_ready_and_truncation_is_visible() -> None:
    models = [f"model-{index}" for index in range(70)]
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:empty",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": [],
                "ready": True,
                "observed_at": 123,
            },
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:many",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1236",
                "models": models,
                "ready": True,
                "observed_at": 123,
            },
        ]
    )

    empty, many = observations
    assert empty["ready"] is False
    assert empty["observed_model_count"] == 0
    assert empty["models_truncated"] is False
    assert many["ready"] is True
    assert many["observed_model_count"] == 64
    assert many["models_truncated"] is True
