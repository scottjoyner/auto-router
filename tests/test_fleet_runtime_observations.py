from __future__ import annotations

import json

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


def test_signed_runtime_identity_witness_is_bounded_but_non_admitting() -> None:
    witness = {
        "schema_version": "fleet-runtime-identity-witness.v1",
        "node_id": "destroyer",
        "runtime_url": "http://localhost:1235",
        "runtime_kind": "llama_cpp",
        "provider_model": "k2-36b",
        "loadout_fingerprint": "sha256:" + "1" * 64,
        "model_content_sha256": "sha256:" + "2" * 64,
        "witness_signer_identity": "runtime-witness-operator",
        "witness_signature_namespace": "lms-runtime-identity-witness",
        "witness_signing_key_fingerprint": "SHA256:trustedkey",
        "witness_fingerprint": "sha256:" + "3" * 64,
        "admission": {"admitted": False},
        "model_file_identity": {
            "device": 1,
            "inode": 2,
            "size_bytes": 123,
            "mtime_ns": 456,
            "ctime_ns": 457,
        },
        "model_process_binding": "proc_maps",
        "process": {
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_sha256": "sha256:" + "4" * 64,
            "executable_basename": "llama-server",
            "executable_file_identity": {
                "device": 10,
                "inode": 11,
                "size_bytes": 12,
                "mtime_ns": 13,
                "ctime_ns": 14,
            },
        },
    }
    payload = json.dumps(witness, sort_keys=True, separators=(",", ":")) + "\n"
    signature = (
        "-----BEGIN SSH SIGNATURE-----\n"
        "bounded-signature\n"
        "-----END SSH SIGNATURE-----\n"
    )
    continuity_document = {
        "schema_version": "fleet-runtime-continuity-attestation.v1",
        "node_id": "destroyer",
        "runtime_observation_id": "runtime-observation:k2",
        "observation": {
            "runtime_observation_id": "runtime-observation:k2",
            "observed_at": 123,
            "runtime_kind": "openai_compatible",
            "protocol": "openai-compatible",
            "base_url": "http://destroyer:1235",
            "models": ["k2-36b"],
            "ready": True,
            "observed_model_count": 1,
        },
        "witness_fingerprint": witness["witness_fingerprint"],
        "runtime_url": "http://localhost:1235",
        "runtime_kind": "llama_cpp",
        "provider_model": "k2-36b",
        "continuity": {
            "valid": True,
            "reason": "match",
            "checked_at": 124,
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_basename": "llama-server",
            "executable_file_valid": True,
            "model_file_valid": True,
            "model_process_binding_valid": True,
            "model_process_binding": "proc_maps",
        },
        "signer_identity": "destroyer",
        "signature_namespace": "lms-runtime-continuity",
        "signing_key_fingerprint": "SHA256:nodekey",
        "admission": {"admitted": False},
        "attestation_fingerprint": "sha256:" + "5" * 64,
    }
    continuity_payload = (
        json.dumps(continuity_document, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    continuity_signature = (
        "-----BEGIN SSH SIGNATURE-----\n"
        "continuity-signature\n"
        "-----END SSH SIGNATURE-----\n"
    )
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:k2",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
                "observed_at": 123,
                "runtime_identity_witness_json": payload,
                "runtime_identity_witness_signature": signature,
                "runtime_identity_continuity_json": continuity_payload,
                "runtime_identity_continuity_signature": continuity_signature,
                "runtime_identity_continuity": {
                    "valid": True,
                    "reason": "match",
                    "checked_at": 124,
                    "pid": 42,
                    "boot_id": "boot",
                    "process_start_ticks": 99,
                    "executable_basename": "llama-server",
                    "executable_file_valid": True,
                    "model_file_valid": True,
                    "model_process_binding_valid": True,
                    "model_process_binding": "proc_maps",
                },
                "admitted": True,
            }
        ]
    )

    assert len(observations) == 1
    item = observations[0]
    assert item["runtime_identity_witness_json"] == payload
    assert item["runtime_identity_witness_signature"] == signature
    assert item["runtime_identity_continuity_json"] == continuity_payload
    assert item["runtime_identity_continuity_signature"] == continuity_signature
    assert item["runtime_identity_continuity"]["valid"] is True
    assert item["runtime_identity_continuity"]["pid"] == 42
    assert item["runtime_identity_continuity"]["executable_file_valid"] is True
    assert item["runtime_identity_continuity"]["model_file_valid"] is True
    assert item["runtime_identity_continuity"]["model_process_binding_valid"] is True
    assert item["runtime_identity_continuity"]["model_process_binding"] == "proc_maps"
    assert item["admitted"] is False


def test_malformed_runtime_identity_witness_is_dropped_without_dropping_observation() -> None:
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:k2",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
                "observed_at": 123,
                "runtime_identity_witness_json": '{"schema_version":"wrong"}',
                "runtime_identity_witness_signature": "not-a-signature",
                "runtime_identity_continuity": {"valid": True},
            }
        ]
    )

    assert len(observations) == 1
    assert "runtime_identity_witness_json" not in observations[0]
    assert "runtime_identity_witness_signature" not in observations[0]
    assert observations[0]["admitted"] is False


def test_cross_runtime_continuity_attestation_is_dropped_but_witness_remains() -> None:
    witness = {
        "schema_version": "fleet-runtime-identity-witness.v1",
        "node_id": "destroyer",
        "runtime_url": "http://localhost:1235",
        "runtime_kind": "llama_cpp",
        "provider_model": "k2-36b",
        "loadout_fingerprint": "sha256:" + "1" * 64,
        "model_content_sha256": "sha256:" + "2" * 64,
        "witness_signer_identity": "runtime-witness-operator",
        "witness_signature_namespace": "lms-runtime-identity-witness",
        "witness_signing_key_fingerprint": "SHA256:trustedkey",
        "witness_fingerprint": "sha256:" + "3" * 64,
        "admission": {"admitted": False},
        "model_file_identity": {
            "device": 1,
            "inode": 2,
            "size_bytes": 123,
            "mtime_ns": 456,
            "ctime_ns": 457,
        },
        "model_process_binding": "proc_maps",
        "process": {
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_sha256": "sha256:" + "4" * 64,
            "executable_basename": "llama-server",
            "executable_file_identity": {
                "device": 10,
                "inode": 11,
                "size_bytes": 12,
                "mtime_ns": 13,
                "ctime_ns": 14,
            },
        },
    }
    witness_payload = json.dumps(
        witness, sort_keys=True, separators=(",", ":")
    ) + "\n"
    continuity = {
        "schema_version": "fleet-runtime-continuity-attestation.v1",
        "node_id": "destroyer",
        "runtime_observation_id": "runtime-observation:OTHER",
        "observation": {
            "runtime_observation_id": "runtime-observation:OTHER",
            "observed_at": 123,
            "runtime_kind": "openai_compatible",
            "protocol": "openai-compatible",
            "base_url": "http://destroyer:1235",
            "models": ["k2-36b"],
            "ready": True,
            "observed_model_count": 1,
        },
        "witness_fingerprint": witness["witness_fingerprint"],
        "continuity": {"valid": True},
        "signer_identity": "destroyer",
        "signature_namespace": "lms-runtime-continuity",
        "signing_key_fingerprint": "SHA256:nodekey",
        "admission": {"admitted": False},
        "attestation_fingerprint": "sha256:" + "5" * 64,
    }
    continuity_payload = json.dumps(
        continuity, sort_keys=True, separators=(",", ":")
    ) + "\n"
    signature = (
        "-----BEGIN SSH SIGNATURE-----\n"
        "bounded\n"
        "-----END SSH SIGNATURE-----\n"
    )

    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:k2",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
                "observed_at": 123,
                "runtime_identity_witness_json": witness_payload,
                "runtime_identity_witness_signature": signature,
                "runtime_identity_continuity_json": continuity_payload,
                "runtime_identity_continuity_signature": signature,
                "runtime_identity_continuity": {"valid": True},
            }
        ]
    )

    assert len(observations) == 1
    item = observations[0]
    assert item["runtime_identity_witness_json"] == witness_payload
    assert "runtime_identity_continuity_json" not in item
    assert "runtime_identity_continuity_signature" not in item
    assert item["admitted"] is False


def test_signed_continuity_with_tampered_model_observation_is_dropped() -> None:
    witness = {
        "schema_version": "fleet-runtime-identity-witness.v1",
        "node_id": "destroyer",
        "runtime_url": "http://localhost:1235",
        "runtime_kind": "llama_cpp",
        "provider_model": "k2-36b",
        "loadout_fingerprint": "sha256:" + "1" * 64,
        "model_content_sha256": "sha256:" + "2" * 64,
        "witness_signer_identity": "runtime-witness-operator",
        "witness_signature_namespace": "lms-runtime-identity-witness",
        "witness_signing_key_fingerprint": "SHA256:trustedkey",
        "witness_fingerprint": "sha256:" + "3" * 64,
        "admission": {"admitted": False},
        "model_file_identity": {
            "device": 1,
            "inode": 2,
            "size_bytes": 123,
            "mtime_ns": 456,
            "ctime_ns": 457,
        },
        "model_process_binding": "proc_maps",
        "process": {
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_sha256": "sha256:" + "4" * 64,
            "executable_basename": "llama-server",
            "executable_file_identity": {
                "device": 10,
                "inode": 11,
                "size_bytes": 12,
                "mtime_ns": 13,
                "ctime_ns": 14,
            },
        },
    }
    witness_payload = json.dumps(
        witness, sort_keys=True, separators=(",", ":")
    ) + "\n"
    continuity = {
        "schema_version": "fleet-runtime-continuity-attestation.v1",
        "node_id": "destroyer",
        "runtime_observation_id": "runtime-observation:k2",
        "observation": {
            "runtime_observation_id": "runtime-observation:k2",
            "observed_at": 123,
            "runtime_kind": "openai_compatible",
            "protocol": "openai-compatible",
            "base_url": "http://destroyer:1235",
            "models": ["different-model"],
            "ready": True,
            "observed_model_count": 1,
        },
        "witness_fingerprint": witness["witness_fingerprint"],
        "continuity": {"valid": True},
        "signer_identity": "destroyer",
        "signature_namespace": "lms-runtime-continuity",
        "signing_key_fingerprint": "SHA256:nodekey",
        "admission": {"admitted": False},
        "attestation_fingerprint": "sha256:" + "5" * 64,
    }
    signature = (
        "-----BEGIN SSH SIGNATURE-----\n"
        "bounded\n"
        "-----END SSH SIGNATURE-----\n"
    )
    observations = fleet_routes._sanitize_runtime_observations(
        [
            {
                "observation_schema": "fleet-runtime-observation.v1",
                "runtime_observation_id": "runtime-observation:k2",
                "runtime_kind": "openai_compatible",
                "protocol": "openai-compatible",
                "base_url": "http://destroyer:1235",
                "models": ["k2-36b"],
                "ready": True,
                "observed_at": 123,
                "runtime_identity_witness_json": witness_payload,
                "runtime_identity_witness_signature": signature,
                "runtime_identity_continuity_json": (
                    json.dumps(continuity, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ),
                "runtime_identity_continuity_signature": signature,
                "runtime_identity_continuity": {"valid": True},
            }
        ]
    )

    assert len(observations) == 1
    item = observations[0]
    assert item["runtime_identity_witness_json"] == witness_payload
    assert "runtime_identity_continuity_json" not in item
    assert item["admitted"] is False
