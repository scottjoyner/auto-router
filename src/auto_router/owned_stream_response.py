"""ASGI response owns the stream until delivery or disconnect."""
import anyio
from starlette.responses import StreamingResponse


async def close_response_body(body) -> None:
    close = getattr(body, "aclose", None)
    if callable(close):
        # Starlette can cancel its streaming task via an AnyIO cancel scope.
        # Cleanup must run outside that cancellation, not abandon the lease.
        with anyio.CancelScope(shield=True):
            await close()


class OwnedStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await close_response_body(self.body_iterator)
