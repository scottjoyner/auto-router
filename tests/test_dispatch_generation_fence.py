"""Exercise exact strict dispatch functions without opening network or app state."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

class Error(Exception):
    def __init__(self, message, *, status_code, retryable):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable

class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def case(self, mode, stream):
        events = []
        model = "fixture-model"
        provider = SimpleNamespace(models=[model], name="fixture")
        candidate = SimpleNamespace(provider=provider, model=model)
        current = SimpleNamespace()
        class Lease:
            async def release(self):
                events.append("release")
        lease = Lease()
        class Admission:
            async def acquire(self, *args):
                events.append("acquire")
                if mode == "queue":
                    current.generation = replacement
                if mode == "expired":
                    current.expired = True
                return lease
        generation = SimpleNamespace(admission=Admission(), providers=SimpleNamespace(enabled=lambda: [provider]))
        replacement = SimpleNamespace(admission=Admission(), providers=generation.providers)
        current.generation = generation
        current.expired = False
        def fresh():
            if current.expired:
                raise RuntimeError("expired")
            return current.generation
        async def claim(*args):
            events.append("claim")
            if mode == "claim":
                current.generation = replacement
        async def select(*args):
            events.append("select")
            if mode == "select":
                current.generation = replacement
            return provider, candidate, None
        async def send(*args, **kwargs):
            events.append("send")
            return SimpleNamespace(body="fixture-body")
        def wrap(body, item):
            self.assertIs(item, lease)
            events.append("wrap")
            return body
        if mode == "stale":
            candidate.provider = SimpleNamespace(models=[model], name="stale")
        path = Path(__file__).parents[1]/"src"/"auto_router"/"main_live.py"
        names = {"_assert_dispatch_generation", "_admitted_dispatch", "_admitted_dispatch_stream"}
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        ns = dict(ProviderError=Error,ProviderCandidate=object,RouterRequest=object,
            ProviderResponse=object,ProviderStreamResponse=object,Any=object,
            _projection_manager=lambda:SimpleNamespace(assert_current_fresh=fresh),
            _admission_controller=lambda:generation.admission,
            assert_executor_claim_current=claim,state=None,_select_provider=select,
            _annotate_request_telemetry=lambda *a:None,_ORIGINAL_DISPATCH=send,
            _ORIGINAL_DISPATCH_STREAM=send,_release_stream_lease=wrap)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),"exec"),ns)
        fn=ns["_admitted_dispatch_stream" if stream else "_admitted_dispatch"]
        request=SimpleNamespace(priority=0)
        if mode=="ok":
            await fn(None,candidate,request)
            self.assertIn("send",events)
            self.assertEqual(events.count("release"),0 if stream else 1)
        else:
            with self.assertRaises(Error) as error:
                await fn(None,candidate,request)
            self.assertEqual(error.exception.status_code,503)
            self.assertTrue(error.exception.retryable)
            self.assertNotIn("send",events)
            self.assertEqual(events.count("release"),0 if mode=="stale" else 1)
    async def test_both_dispatch_paths(self):
        for stream in (False,True):
            for mode in ("ok","stale","queue","expired","claim","select"):
                with self.subTest(mode=mode,stream=stream):
                    await self.case(mode,stream)

if __name__=="__main__":
    unittest.main()
