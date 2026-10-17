"""Residency must be reported as unknown, not false, when it cannot be determined.

A bare ``llama-server`` lane exposes no LM Studio native API: ``/api/v1/models``
returns 404. The LM Studio adapter then falls back to the OpenAI-compatible
``/v1/models``, which lists the model the server is actually serving but carries
**no residency field at all** -- an entry is only ``{id, object, owned_by}``.

Consumers infer residency with ``bool(entry.get("loaded"))``, which is False for
such a record. So the router concludes that a working lane is idle: a
``destroyer-ornith`` or ``lenovo-lfm-cpu`` lane that is serving traffic is
reported as having nothing loaded.

The distinction matters. ``False`` is a measurement -- "this model is downloaded
and not resident". ``None``/unknown is the honest state, and treating unknown as
false is what makes a model swap invisible to the routing refresh described in
``live_model_routes.refresh_provider_models``.

These tests pin the distinction. They should fail against the fallback path as
it stands.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "pytest_httpx", reason="pytest-httpx is a dev dependency; install with -e '.[dev]'"
)

import httpx
from pytest_httpx import HTTPXMock

from auto_router.models import ProviderConfig
from auto_router.providers import build_provider


@pytest.fixture
def llama_server_config() -> ProviderConfig:
    """An lmstudio provider pointed at a bare llama-server, as destroyer/lenovo are."""
    return ProviderConfig(
        name="lmstudio-destroyer-ornith",
        type="lmstudio",
        base_url="http://destroyer.example:1236/v1",
        quota_class="local",
    )


@pytest.mark.anyio
async def test_llama_server_lane_does_not_report_loaded_false(
    httpx_mock: HTTPXMock, llama_server_config: ProviderConfig
) -> None:
    """A 404 from the native API must not become `loaded: False`."""
    httpx_mock.add_response(
        url="http://destroyer.example:1236/api/v1/models", status_code=404
    )
    httpx_mock.add_response(
        url="http://destroyer.example:1236/v1/models",
        json={
            "object": "list",
            "data": [
                {"id": "destroyer-ornith", "object": "model", "owned_by": "llamacpp"}
            ],
        },
    )

    provider = build_provider(llama_server_config, timeout_seconds=5.0)
    records = await provider.list_models()

    assert records, "the fallback should still list the served model"
    record = records[0]

    # The reason must be machine-readable, not inferred later.
    assert record.get("residency") == "unknown", (
        "a record produced by the OpenAI-compatible fallback should declare "
        "residency='unknown' so routing can tell it apart from a measured unload"
    )
    assert record.get("source") == "openai_compat_fallback"

    # The consumer is where the defect actually bites. model_placement._is_loaded
    # ends in `bool(entry.get("loaded"))`, so a record with no `loaded` key is
    # coerced to False -- "downloaded and not resident" -- for a lane that is
    # demonstrably serving. Assert against that coercion, not just the record shape.
    from auto_router.model_placement import _is_loaded

    assert _is_loaded(record) is not False, (
        "a bare llama-server lane provides no residency signal, so _is_loaded "
        "must not report False; unknown is not the same as measured-unloaded"
    )


@pytest.mark.anyio
async def test_native_lmstudio_reports_measured_residency(
    httpx_mock: HTTPXMock,
) -> None:
    """The native path is unaffected: residency there is a real measurement."""
    httpx_mock.add_response(
        url="http://localhost:1234/api/v1/models",
        json={
            "models": [
                {
                    "key": "liquid/lfm2.5-1.2b",
                    "publisher": "liquid",
                    "loaded_instances": [
                        {"identifier": "lmstudio:1234/abc", "config": {"context_length": 8192}}
                    ],
                },
                {
                    "key": "prism-ml/bonsai-27b",
                    "publisher": "prism-ml",
                    "loaded_instances": [],
                },
            ]
        },
    )
    config = ProviderConfig(
        name="lmstudio-beelink", type="lmstudio",
        base_url="http://localhost:1234/v1", quota_class="local",
    )

    provider = build_provider(config, timeout_seconds=5.0)
    records = {r["id"]: r for r in await provider.list_models()}

    assert records["liquid/lfm2.5-1.2b"]["loaded"] is True
    assert records["prism-ml/bonsai-27b"]["loaded"] is False
    # A measured False must remain distinguishable from an unknown.
    assert records["prism-ml/bonsai-27b"].get("residency") in (None, "measured")
    assert records["liquid/lfm2.5-1.2b"].get("source") == "lmstudio_native"