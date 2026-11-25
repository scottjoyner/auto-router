"""Isolated tests of the strict entrypoint's stream wrapper, without app startup."""
import ast
from pathlib import Path
from collections.abc import AsyncIterator
import pytest

def wrapper():
    path=Path(__file__).parents[1]/"src"/"auto_router"/"main_live.py"
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="_release_stream_lease")
    ns={"AsyncIterator":AsyncIterator,"RuntimeAdmissionLease":object}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),"exec"),ns)
    return ns["_release_stream_lease"]

@pytest.mark.asyncio
@pytest.mark.parametrize("fail_close",[False,True])
async def test_partial_close_releases_after_upstream_close(fail_close):
    events=[]
    class Lease:
        async def release(self):events.append("released")
    async def body():
        try:
            yield b"partial"
            yield b"more"
        finally:
            events.append("upstream_closed")
            if fail_close:raise OSError("synthetic close failure")
    wrapped=wrapper()(body(),Lease())
    assert await anext(wrapped)==b"partial"
    if fail_close:
        with pytest.raises(OSError,match="synthetic close failure"):await wrapped.aclose()
    else:
        await wrapped.aclose()
    assert events==["upstream_closed","released"]
