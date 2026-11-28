from types import SimpleNamespace as NS

import pytest
from auto_router.admission import RuntimeAdmissionError
from auto_router.claim_fence import ExecutorClaimFenceError
from auto_router.request_idempotency import RequestIdempotencyLedger
from auto_router.stream_lifecycle import install_stream_lifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "error",
    [RuntimeAdmissionError("queue full", status_code=429), ExecutorClaimFenceError("revoked")],
)
@pytest.mark.parametrize("prior", ["in_progress", "possibly_accepted"])
async def test_local_refusal_preserves_prior_state(tmp_path, stream, error, prior):
    ledger = RequestIdempotencyLedger(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    ledger.reserve("k", "fp", "req")
    if prior != "in_progress":
        ledger.transition("k", prior, detail="prior upstream uncertainty")

    async def refused(*args, **kwargs):
        raise error

    module = NS(
        state=NS(request_idempotency=ledger),
        _dispatch=refused,
        _dispatch_stream=refused,
        _record_usage=lambda *a, **k: None,
    )
    install_stream_lifecycle(module, upstream_start_in_dispatch=True)
    request = NS(metadata={"idempotency_key": "k"})
    with pytest.raises(type(error)):
        await (module._dispatch_stream if stream else module._dispatch)(None, None, request)
    assert ledger.get("k")["state"] == prior


def test_prior_uncertainty_cannot_be_erased_by_retry(tmp_path):
    ledger = RequestIdempotencyLedger(f"sqlite:///{tmp_path / 'ledger.sqlite3'}")
    ledger.reserve("k", "fp", "req")
    ledger.transition("k", "possibly_accepted", detail="prior uncertainty")
    for status in ("upstream_started", "in_progress", "failed", "cancelled"):
        ledger.transition("k", status)
        assert ledger.get("k")["state"] == "possibly_accepted"
