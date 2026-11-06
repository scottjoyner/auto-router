"""Source-only, socketless regression for deprecated self-admin backlog scheduler.

Legacy main attempted recurring unauthenticated POST to an admin route. It
must not be revived via a shared operator token; signed machine Task admission
requires a different, independent authority. This test never imports the app.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "auto_router"


def _source(module: str) -> str:
    return (SRC / module).read_text(encoding="utf-8")


def _functions(code: str):
    return {
        node.name: node for node in ast.parse(code).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _schedules_legacy_backlog(code: str) -> bool:
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                if arg.func.id == "_backlog_burn_loop":
                    return True
    return False


def test_no_untrusted_admin_self_post_in_router_startup():
    code = _source("main.py")
    funcs = _functions(code)
    assert "_backlog_burn_loop" not in funcs
    assert not _schedules_legacy_backlog(code)
    assert "await client.post(" not in code or (
        "/admin/backlog/burn-down" not in code.split("await client.post(")[-1]
    )
    startup = funcs["lifespan"]
    assignments = [
        n for n in ast.walk(startup)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "backlog_burn_task" for t in n.targets)
    ]
    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value is None


def test_other_reconciled_entrypoints_have_no_legacy_backlog_scheduler():
    for module in ("main_live.py", "secure_live.py"):
        code = _source(module)
        assert not _schedules_legacy_backlog(code)
        assert "_backlog_burn_loop" not in _functions(code)


def test_authenticated_admin_paths_still_present_and_ordinary_dispatch_intact():
    code = _source("main.py")
    # This removes only the phantom admin self-POST. The ordinary scoped
    # event-outbox delivery and externally authenticated routes stay intact.
    assert "async def outbox_dispatch_task" in code
    assert "dispatch_task = asyncio.create_task(outbox_dispatch_task())" in code
    assert "from auto_router.security import require_admin" in code
    # Full mounted/compiled admin authentication is separately enforced by
    # tests/test_admin_mounted_runtime_contract.py on every entrypoint.


def test_negative_fixture_catches_old_unapproved_autoburn():
    old = """
async def legacy():
    asyncio.create_task(_backlog_burn_loop())
"""
    assert _schedules_legacy_backlog(old)
