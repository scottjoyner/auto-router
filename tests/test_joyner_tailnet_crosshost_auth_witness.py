"""Offline fixed-destination cross-host catalog boundary tests.

No live network calls, production credentials or inference activity.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from scripts import joyner_tailnet_crosshost_auth_witness as witness


def test_four_fixed_targets_and_expected_security_classes():
    seen = []
    statuses = {1234: 200, 1236: 200, 18320: 401, 18321: 403}

    def fake(target):
        seen.append(target)
        return statuses[target.port], None

    report = witness.audit(hostname="x1-370", probe=fake)
    assert len(seen) == 4
    assert [x.port for x in seen] == [1234, 1236, 18320, 18321]
    assert all(x.host == witness.HOST for x in seen)
    assert report["verdict"] == "PERIMETER_NO_GO"
    assert report["release_gate"] == "HOLD"
    assert report["tailnet_peer_reachability_observed"]
    assert report["unauthenticated_catalogs_observed"] == [
        "minicpm-catalog", "ling-catalog"
    ]
    assert report["guarded_catalogs_observed"] == [
        "guarded-minicpm-catalog", "guarded-toolcall-catalog"
    ]
    assert not report["public_internet_reachability_verified"]
    assert not report["unauthorized_client_reachability_verified"]
    assert not report["router_promotion_authorized"]
    assert not report["gpu_allocation_authorized"]
    assert not report["service_change_authorized"]


def test_wrong_host_never_probes():
    def forbidden(target):
        raise AssertionError("unapproved network call")

    report = witness.audit(hostname="joyner", probe=forbidden)
    assert report["endpoints"] == []
    assert not report["observer_host_verified"]
    assert not report["tailnet_peer_reachability_observed"]
    assert report["release_gate"] == "HOLD"


@pytest.mark.parametrize("status,expected", [
    (200, "CATALOG_READABLE_WITHOUT_CREDENTIAL"),
    (401, "MISSING_CREDENTIAL_REJECTED"),
    (403, "MISSING_CREDENTIAL_REJECTED"),
    (302, "UNEXPECTED_HTTP_RESPONSE"),
    (500, "UNEXPECTED_HTTP_RESPONSE"),
    (None, "UNVERIFIED"),
])
def test_status_classification(status, expected):
    assert witness.classify(status) == expected


def test_failure_has_no_false_reachability():
    report = witness.audit(
        hostname="x1-370",
        probe=lambda _: (None, "transport_unverified"),
    )
    assert not report["tailnet_peer_reachability_observed"]
    assert report["unauthenticated_catalogs_observed"] == []
    assert all(x["classification"] == "UNVERIFIED" for x in report["endpoints"])
    assert report["release_gate"] == "HOLD"


def test_only_status_recorded_never_sensitive_exception_string():
    secret = "SYNTHETIC-SECRET-NEVER-LOG"
    def fail(target):
        raise OSError("network failure contains " + secret)

    report = witness.audit(hostname="x1-370", probe=fail)
    payload = json.dumps(report)
    assert secret not in payload
    assert witness.HOST not in payload
    assert not report["credentials_sent"]
    assert not report["model_generation_performed"]


def test_invalid_destination_and_name_rejected_before_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network invoked")
    monkeypatch.setattr(witness, "build_opener", no_network)
    with pytest.raises(ValueError, match="unauthorized_catalog_target"):
        witness.observe(witness.CatalogTarget(
            "minicpm-catalog", "evil.example.test", 1234,
        ))
    with pytest.raises(ValueError, match="unauthorized_catalog_target"):
        witness.observe(witness.CatalogTarget(
            "arbitrary", witness.HOST, 1234,
        ))
    with pytest.raises(ValueError, match="unauthorized_catalog_target"):
        witness.observe(witness.CatalogTarget(
            "minicpm-catalog", witness.HOST, 9000,
        ))


def test_exact_request_and_no_body_ever_read(monkeypatch):
    seen = []

    class FakeResponse:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, *a, **kw):
            raise AssertionError("no response body read")

    class FakeOpener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return FakeResponse()

    def fake_build_opener(*handlers):
        assert any(isinstance(h, witness.ProxyHandler) for h in handlers)
        assert any(isinstance(h, witness._DenyRedirect) for h in handlers)
        return FakeOpener()

    monkeypatch.setattr(witness, "build_opener", fake_build_opener)
    result = witness.observe(witness.CatalogTarget(
        "guarded-minicpm-catalog", witness.HOST, 18320,
    ))
    assert result == (401, None)
    assert len(seen) == 1
    req, timeout = seen[0]
    assert req.get_method() == "GET"
    assert req.full_url == f"http://{witness.HOST}:18320/v1/models"
    assert timeout == witness.TIMEOUT_SECONDS
    assert all(key.lower() != "authorization" for key, val in req.header_items())
    assert req.data is None


def test_redirect_handler_refuses_second_target():
    deny = witness._DenyRedirect()
    assert deny.redirect_request(None, None, 302, "moved", {}, "http://other") is None


def test_http_redirect_status_is_not_followed(monkeypatch):
    class FakeOpener:
        def open(self, req, timeout):
            raise HTTPError(req.full_url, 302, "moved", {"Location": "http://evil"}, None)

    monkeypatch.setattr(witness, "build_opener", lambda *a: FakeOpener())
    status, err = witness.observe(witness.CatalogTarget(
        "minicpm-catalog", witness.HOST, 1234,
    ))
    assert status == 302
    assert err is None
    assert witness.classify(status) == "UNEXPECTED_HTTP_RESPONSE"


def test_no_credential_errbacks_and_status_codes(monkeypatch):
    class FakeOpener:
        def open(self, req, timeout):
            raise URLError("SYNTHETIC-TOKEN-IN-ERROR")

    monkeypatch.setattr(witness, "build_opener", lambda *a: FakeOpener())
    status, err = witness.observe(witness.CatalogTarget(
        "minicpm-catalog", witness.HOST, 1234,
    ))
    assert status is None and err == "transport_unverified"


def test_bad_prober_return_is_never_reachability_proof():
    report = witness.audit(hostname="x1-370", probe=lambda _: (700, None))
    assert not report["tailnet_peer_reachability_observed"]
    assert all(x["http_without_credential"] is None for x in report["endpoints"])
    assert not report["router_promotion_authorized"]


def test_main_prints_one_json_object_and_always_holds(monkeypatch, capsys):
    monkeypatch.setattr(witness, "audit", lambda: {
        "schema": witness.SCHEMA, "release_gate": "HOLD"
    })
    assert witness.main() == 78
    report = json.loads(capsys.readouterr().out)
    assert report["release_gate"] == "HOLD"
