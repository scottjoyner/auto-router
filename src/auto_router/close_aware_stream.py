"""Explicit close for an iterator whose resources were acquired before iteration."""
from collections.abc import AsyncIterator, Awaitable, Callable


class CloseAwareStream:
    """Wrap an async generator that owns cleanup once iteration has begun.

    Before first iteration, generator.aclose() does not execute its finally block.
    Invoke the separate pre-start cleanup instead. Consumers must serialize reads
    and close, as with an async generator. This is local cleanup, not proof that
    upstream inference stopped.
    """

    def __init__(
        self,
        iterator: AsyncIterator[bytes],
        close_unstarted: Callable[[], Awaitable[None]],
    ) -> None:
        self._iterator = iterator
        self._close_unstarted = close_unstarted
        self._started = False
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        self._started = True
        try:
            return await anext(self._iterator)
        except BaseException:
            # The underlying generator owns its finally path after first read.
            self._closed = True
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            close = getattr(self._iterator, "aclose", None)
            if callable(close):
                await close()
        else:
            await self._close_unstarted()
