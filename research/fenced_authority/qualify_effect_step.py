#!/usr/bin/env python3
"""Isolated live-epoch SQL *observation* trial. No provider dispatch.

Explicit --run only. Uses a cached postgres:17-bookworm image, a disposable
network-disabled container, and synthetic hashes. Each stream step is a DB
check, not an atomic lease over a subsequent model/provider operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import qualify_pg as base

ROOT = Path(__file__).resolve().parent
IMAGE = "postgres:17-bookworm"
CONTAINER = "router-fence-effect-step-20261024"


def run() -> dict:
    base.IMAGE = IMAGE
    base.CONTAINER = CONTAINER
    if CONTAINER in base.shell(
        ["docker", "ps", "-a", "--format", "{{.Names}}"]
    ).splitlines():
        raise RuntimeError("NO_GO disposable effect container name in use")
    image_id = base.shell(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    schema = (ROOT / "schema.sql").read_bytes()
    supplement = (ROOT / "effect_step_check.sql").read_bytes()
    if not schema.startswith(b"-- EXPERIMENT ONLY") or not supplement.startswith(
        b"-- EXPERIMENT ONLY"
    ):
        raise RuntimeError("NO_GO untrusted research source")
    created = False
    try:
        base.shell([
            "docker", "run", "-d", "--name", CONTAINER, "--network", "none",
            "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "2",
            "--pids-limit", "128", "--security-opt", "no-new-privileges:true",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e", "POSTGRES_INITDB_ARGS=--auth-local=trust", IMAGE,
        ])
        created = True
        for _ in range(30):
            probe = subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True, text=True, timeout=4, check=False,
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("NO_GO disposable postgres not ready")

        base.psql(schema.decode())
        base.psql(supplement.decode())
        base.psql("CREATE ROLE fence_effect_worker NOLOGIN;")
        privilege = base.query_single(
            "SELECT has_function_privilege("
            "'fence_effect_worker',"
            "'fence_research.assert_live_effect_step(text,bigint,text,text,"
            "bigint,text,text,text)','EXECUTE');"
        )
        assert privilege == "f", "untrusted worker can call SQL step check"

        # All content is synthetic, and the database is disposable.
        scope = "a" * 64
        owner = "b" * 64
        claim = "c" * 64
        jti = "d" * 64
        req = "e" * 64
        model = "f" * 64
        runtime = "1" * 64
        expiry = base.query_single(
            "SELECT (clock_timestamp()+interval '50 seconds')::text;"
        )
        token_expiry = base.query_single(
            "SELECT (clock_timestamp()+interval '45 seconds')::text;"
        )

        def grant() -> str:
            return base.query_single(
                "SELECT fence_research.grant_scope("
                f"'{scope}','{owner}','{claim}',81,"
                f"'{expiry}'::timestamptz);"
            )

        def reserve(epoch: int, identity: str, request: str) -> str:
            return base.query_single(
                "SELECT fence_research.reserve("
                f"'{scope}',{epoch},'{owner}','{claim}',81,"
                f"'{identity}','{token_expiry}'::timestamptz,2,"
                f"'{request}','{model}','{runtime}');"
            )

        def check(epoch: int, *, request: str = req, model_id: str = model,
                  owner_id: str = owner) -> str:
            return base.query_single(
                "SELECT fence_research.assert_live_effect_step("
                f"'{scope}',{epoch},'{owner_id}','{claim}',81,"
                f"'{request}','{model_id}','{runtime}');"
            )

        assert grant() == "1"
        assert reserve(1, jti, req) == "1"
        # Only a synthetic step is checked. No model or HTTP effect.
        for _ in range(3):
            assert check(1) == "t"
        for kwargs in (
            {"request": "2" * 64},
            {"model_id": "3" * 64},
            {"owner_id": "4" * 64},
        ):
            base.expect_fail(
                "SELECT fence_research.assert_live_effect_step("
                f"'{scope}',1,'{kwargs.get('owner_id',owner)}','{claim}',81,"
                f"'{kwargs.get('request',req)}',"
                f"'{kwargs.get('model_id',model)}','{runtime}');",
                message="effect_check_denied",
            )

        # Real PostgreSQL revocation blocks the NEXT stream check on one
        # primary. It does not stop any external effect between these calls.
        assert base.query_single(
            "SELECT fence_research.revoke_scope("
            f"'{scope}',1,'{owner}','{claim}');"
        ) == "t"
        base.expect_fail(
            "SELECT fence_research.assert_live_effect_step("
            f"'{scope}',1,'{owner}','{claim}',81,"
            f"'{req}','{model}','{runtime}');",
            message="effect_check_denied: scope revoked",
        )

        assert grant() == "2"
        new_jti = "5" * 64
        new_req = "6" * 64
        assert reserve(2, new_jti, new_req) == "1"
        assert check(2, request=new_req) == "t"
        base.expect_fail(
            "SELECT fence_research.assert_live_effect_step("
            f"'{scope}',1,'{owner}','{claim}',81,"
            f"'{req}','{model}','{runtime}');",
            message="effect_check_denied",
        )

        # Terminal status is immutable. No more stream steps after a
        # terminal receipt even in the same valid epoch.
        assert base.query_single(
            "SELECT fence_research.commit_terminal("
            f"'{scope}',2,'{owner}','{claim}','{new_req}','{model}',"
            f"'{runtime}','completed','{'7'*64}');"
        ) == "recorded"
        base.expect_fail(
            "SELECT fence_research.assert_live_effect_step("
            f"'{scope}',2,'{owner}','{claim}',81,"
            f"'{new_req}','{model}','{runtime}');",
            message="effect_check_denied: no live matching reserved request",
        )

        # Test database loss: a caller must fail closed and cannot treat
        # the previous true result as a permit for additional stream steps.
        base.shell(["docker", "stop", CONTAINER], timeout=15)
        inaccessible = False
        try:
            check(2, request=new_req)
        except RuntimeError:
            inaccessible = True
        assert inaccessible, "NO_GO: disconnected database falsely healthy"

        return {
            "decision": "PASS_SINGLE_PRIMARY_STEP_OBSERVATION_NOT_PROVIDER_FENCE",
            "postgres_image": IMAGE,
            "postgres_image_sha256": image_id,
            "schema_sha256": hashlib.sha256(schema).hexdigest(),
            "supplement_sha256": hashlib.sha256(supplement).hexdigest(),
            "successful_synthetic_pre_revoke_checks": 3,
            "wrong_identity_model_request_denied": True,
            "revoked_stream_step_denied": True,
            "old_epoch_after_regrant_denied": True,
            "terminal_stream_step_denied": True,
            "primary_unreachable_fails_closed": inaccessible,
            "worker_sql_exec_denied": True,
            "network": "none",
            "published_ports": 0,
            "provider_calls": 0,
            "worker_models_started": 0,
            "live_resource_effect_atomicity_proven": False,
            "cross_primary_highwater_proven": False,
            "execution_authorized": False,
            "container_removed_on_exit": True,
        }
    finally:
        if created:
            removal = subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if removal.returncode:
                raise RuntimeError("CRITICAL disposable step container cleanup failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("PLAN_ONLY: no database, model request, container or network started")
        return
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
