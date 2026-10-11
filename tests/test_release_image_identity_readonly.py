"""Synthetic, no-container tests of exact-image cutover fencing."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "verify_release_image_identity_readonly.py"
spec = importlib.util.spec_from_file_location("release_img", SOURCE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

C = "sha256:" + "a" * 64
R = "sha256:" + "b" * 64
S = "c" * 40


def inspect(manifest=None, **changes):
    args = dict(
        compose=manifest or {"services": {"llm-router": {"image": "git-router@sha256:" + "d"*64}}},
        service="llm-router", candidate_image_id=C, rollback_image_id=R,
        reviewed_source_sha=S, observed_running_image_id=R,
        observed_compose_image_id=C,
        candidate_embedded_revision=S, candidate_present=True, rollback_present=True,
    )
    args.update(changes)
    return app.inspect_release_structure(**args)


def test_exact_image_and_revision_metadata_are_necessary_but_not_release_authority():
    r = inspect()
    assert r["decision"] == "PASS_IDENTITY_PREFLIGHT_ONLY"
    assert r["deployment_authorized"] is False
    assert r["router_auth_verified"] is False
    assert r["credential_custody_verified"] is False


@pytest.mark.parametrize("tag", ["git-router", "git-router:latest", "git-router:v1", "", "git-router@not-a-digest"])
def test_floating_compose_tag_always_blocks(tag):
    assert "COMPOSE_IMAGE_NOT_IMMUTABLY_PINNED" in inspect(
        {"services": {"llm-router": {"image": tag}}}
    )["reason_codes"]


@pytest.mark.parametrize("changes,reason", [
    ({"candidate_present": False}, "CANDIDATE_IMAGE_NOT_LOCALLY_AVAILABLE"),
    ({"rollback_present": False}, "ROLLBACK_IMAGE_NOT_LOCALLY_AVAILABLE"),
    ({"candidate_embedded_revision": "other"}, "IMAGE_REVISION_LABEL_DOES_NOT_MATCH_REVIEWED_SOURCE"),
    ({"candidate_image_id": R}, "CANDIDATE_EQUALS_ROLLBACK"),
    ({"observed_running_image_id": C}, "RUNNING_IMAGE_DIFFERS_FROM_ROLLBACK_REFERENCE"),
    ({"reviewed_source_sha": "main"}, "REVIEWED_SOURCE_SHA_INVALID"),
    ({"candidate_image_id": "git-router:latest"}, "CANDIDATE_IMAGE_ID_INVALID"),
    ({"rollback_image_id": "sha256:abc"}, "ROLLBACK_IMAGE_ID_INVALID"),
])
def test_missing_identity_metadata_is_hold(changes, reason):
    assert reason in inspect(**changes)["reason_codes"]


@pytest.mark.parametrize("resolved_id", ["", R, "sha256:" + "e" * 64, "garbage"])
def test_compose_digest_must_resolve_to_reviewed_candidate_id(resolved_id):
    report = inspect(observed_compose_image_id=resolved_id)
    assert report["decision"] == "HOLD_IMAGE_IDENTITY"
    assert "COMPOSE_DIGEST_DOES_NOT_RESOLVE_TO_CANDIDATE" in report["reason_codes"]
    assert report["deployment_authorized"] is False


def test_immutable_digest_syntax_does_not_establish_candidate_mapping():
    # A valid pin naming a different local image must never qualify the chosen candidate.
    manifest = {"services": {"llm-router": {"image": "git-router@sha256:" + "e" * 64}}}
    report = inspect(manifest, observed_compose_image_id=R)
    assert "COMPOSE_DIGEST_DOES_NOT_RESOLVE_TO_CANDIDATE" in report["reason_codes"]


def test_missing_or_buildable_service_never_certifies_image():
    assert "SERVICE_NOT_PRESENT" in inspect({"services": {}})["reason_codes"]
    assert "COMPOSE_HAS_BUILD_STEP" in inspect(
        {"services": {"llm-router": {"image": "git-router@sha256:" + "d"*64, "build": "."}}}
    )["reason_codes"]


def test_report_includes_no_paths_values_or_image_shas():
    report = str(inspect())
    for secret_like in (C, R, S, "git-router@sha256:"):
        assert secret_like not in report
