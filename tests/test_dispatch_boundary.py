import asyncio
from types import SimpleNamespace as NS

import pytest
from auto_router.admission import RuntimeAdmissionError
from auto_router.claim_fence import ExecutorClaimFenceError
from auto_router.request_idempotency import RequestIdempotencyLedger

@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('outcome', ['queue_full', 'revoked', 'dispatch'])
async def test_actual_admitted_dispatch_boundary(monkeypatch, tmp_path, stream, outcome):
    import auto_router.main_live as live
    ledger = RequestIdempotencyLedger(f'sqlite:///{tmp_path / "ledger.sqlite3"}')
    ledger.reserve('k', 'fp', 'req')
    monkeypatch.setattr(live, 'state', NS(request_idempotency=ledger))
    monkeypatch.setattr(live, '_projection_manager', lambda: NS(assert_current_fresh=lambda: None))
    released=[]; called=[]
    async def release(): released.append(True)
    lease=NS(release=release)
    async def acquire(*args):
        if outcome == 'queue_full': raise RuntimeAdmissionError('queue full',status_code=429)
        return lease
    async def claim(*args):
        if outcome == 'revoked': raise ExecutorClaimFenceError('revoked')
    async def select(candidate): return None,candidate,None
    async def upstream(*args, **kwargs):
        called.append(ledger.get('k')['state'])
        if stream:
            async def body(): yield b'data: [DONE]\n\n'
            return NS(body=body())
        return NS(status_code=200)
    monkeypatch.setattr(live,'_admission_controller',lambda:NS(acquire=acquire))
    monkeypatch.setattr(live,'assert_executor_claim_current',claim)
    monkeypatch.setattr(live,'_select_provider',select)
    monkeypatch.setattr(live,'_annotate_request_telemetry',lambda *args:None)
    monkeypatch.setattr(live,'_ORIGINAL_DISPATCH',upstream)
    monkeypatch.setattr(live,'_ORIGINAL_DISPATCH_STREAM',upstream)
    request=NS(metadata={'idempotency_key':'k'},priority=None)
    dispatch=live._admitted_dispatch_stream if stream else live._admitted_dispatch
    if outcome!='dispatch':
        with pytest.raises(RuntimeAdmissionError if outcome=='queue_full' else ExecutorClaimFenceError): await dispatch(None,None,request)
        assert ledger.get('k')['state']=='in_progress'
        assert called==[]
        assert len(released)==(outcome=='revoked')
    else:
        response=await dispatch(None,None,request)
        if stream: assert [chunk async for chunk in response.body]==[b'data: [DONE]\n\n']
        assert called==['upstream_started']
        assert released==[True]
