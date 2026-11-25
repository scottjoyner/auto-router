import asyncio
import pytest
from starlette.requests import ClientDisconnect
from auto_router.close_aware_stream import CloseAwareStream
from auto_router.owned_stream_response import OwnedStreamingResponse

def scope():
    return {"type":"http","asgi":{"version":"3.0","spec_version":"2.4"}}

@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at",["http.response.start","http.response.body",None])
async def test_owned_response_closes_on_send_failure_or_completion(fail_at):
    events=[]
    async def before_start():
        events.append("closed-before-read")
    async def chunks():
        try:
            events.append("read")
            yield b"one"
            yield b"two"
        finally:
            events.append("closed-after-read")
    body=CloseAwareStream(chunks(),before_start)
    response=OwnedStreamingResponse(body)
    async def receive():
        await asyncio.sleep(3600)
    async def send(message):
        if message["type"]==fail_at:
            raise OSError("synthetic disconnect")
    if fail_at:
        with pytest.raises((OSError,ClientDisconnect)):
            await response(scope(),receive,send)
    else:
        await response(scope(),receive,send)
    assert events==(["closed-before-read"] if fail_at=="http.response.start" else ["read","closed-after-read"])
    await body.aclose()
    assert events.count("closed-before-read")+events.count("closed-after-read")==1
