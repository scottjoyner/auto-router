#!/usr/bin/env python3
"""Disposable PostgreSQL broker/worker ACL experiment. NO LIVE ADMISSION.

Runs only with --run against locally cached postgres:17-bookworm.
All database roles, rows, functions and queries are synthetic. No port,
volume, production credentials, model inference, or service activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
IMAGE = "postgres:17-bookworm"
CONTAINER = "router-fence-broker-acl-20261024"
OWNER_ROLE = "fence_acl_owner"
BROKER_ROLE = "fence_acl_broker"
WORKER_ROLE = "fence_acl_worker"
ROUTINES = (
    "grant_scope(text,text,text,bigint,timestamptz)",
    "revoke_scope(text,bigint,text,text)",
    "reserve(text,bigint,text,text,bigint,text,timestamptz,integer,text,text,text)",
    "commit_terminal(text,bigint,text,text,text,text,text,text,text)",
)


def cmd(args: list[str], *, sql: str | None = None, timeout: int = 25) -> str:
    done = subprocess.run(args, input=sql, capture_output=True, text=True,
                          timeout=timeout, check=False)
    if done.returncode:
        raise RuntimeError("disposable command failed: " + done.stderr[-350:])
    return done.stdout.strip()


def psql(sql: str) -> str:
    return cmd(
        ["docker", "exec", "-i", CONTAINER, "psql", "-X",
         "-v", "ON_ERROR_STOP=1", "-U", "postgres",
         "-d", "postgres", "-t", "-A", "-q"],
        sql=sql + "\n",
    )


def denied(sql: str, expected: str) -> None:
    try:
        psql(sql)
    except RuntimeError as exc:
        if expected not in str(exc):
            raise AssertionError(
                "wrong refusal, wanted " + expected + ": " + str(exc)
            ) from exc
        return
    raise AssertionError("untrusted SQL succeeded: " + expected)


def run() -> dict:
    names = cmd(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    if CONTAINER in names:
        raise RuntimeError("NO_GO disposable container name already in use")
    imageid = cmd(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    schema = (ROOT / "schema.sql").read_bytes()
    if not schema.startswith(b"-- EXPERIMENT ONLY") or (
        b"ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;" not in schema
    ):
        raise RuntimeError("NO_GO pinned safe research schema missing")
    created = False
    try:
        cmd(
            ["docker", "run", "-d", "--name", CONTAINER,
             "--network", "none", "--memory", "1024m",
             "--memory-swap", "1024m", "--cpus", "2",
             "--pids-limit", "128", "--security-opt", "no-new-privileges:true",
             "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
             "-e", "POSTGRES_INITDB_ARGS=--auth-local=trust",
             IMAGE],
        )
        created = True
        for _ in range(30):
            ready = subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True, text=True, timeout=4, check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("disposable postgres was not ready")

        psql(
            f"CREATE ROLE {OWNER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;"
            f"CREATE ROLE {BROKER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;"
            f"CREATE ROLE {WORKER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;"
        )
        # PG 15+ revokes database CREATE from PUBLIC. Give the disposable
        # NONLOGIN owner creation privileges in this test DB only; never
        # grant CREATE to the broker or worker.
        psql("GRANT CREATE ON DATABASE postgres TO " + OWNER_ROLE + ";")
        # The actual schema is unmodified and executed as a non-superuser.
        # Only the disposable test augments the approved routines as definer.
        psql("SET ROLE " + OWNER_ROLE + ";\n" + schema.decode() + "\nRESET ROLE;")
        changes = ["SET ROLE " + OWNER_ROLE + ";"]
        for signature in ROUTINES:
            routine = "fence_research." + signature
            changes.extend((
                "ALTER FUNCTION " + routine + " SECURITY DEFINER;",
                "ALTER FUNCTION " + routine
                + " SET search_path TO pg_catalog, pg_temp;",
            ))
        changes.append("GRANT USAGE ON SCHEMA fence_research TO " + BROKER_ROLE + ";")
        changes.extend(
            "GRANT EXECUTE ON FUNCTION fence_research." + signature
            + " TO " + BROKER_ROLE + ";"
            for signature in ROUTINES
        )
        changes.append("RESET ROLE;")
        psql("\n".join(changes))

        # The role executing the approved SECURITY DEFINER function is
        # NOT allowed to read, update or own the underlying tables.
        for table in ("scopes", "attempts", "executions"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                acl = psql(
                    "SELECT has_table_privilege("
                    f"'{BROKER_ROLE}','fence_research.{table}','{privilege}');"
                )
                assert acl == "f", (table, privilege, acl)
        owner_att = psql(
            "SELECT rolsuper::text || ':' || rolcreaterole::text "
            f"FROM pg_roles WHERE rolname='{OWNER_ROLE}';"
        )
        assert owner_att == "false:false", owner_att
        for signature in ROUTINES:
            acl = psql(
                "SELECT has_function_privilege("
                f"'{BROKER_ROLE}', 'fence_research.{signature}', 'EXECUTE');"
            )
            assert acl == "t", (signature, acl)
            worker_acl = psql(
                "SELECT has_function_privilege("
                f"'{WORKER_ROLE}', 'fence_research.{signature}', 'EXECUTE');"
            )
            assert worker_acl == "f", (signature, worker_acl)
            flags = psql(
                "SELECT p.prosecdef::text || ':' || "
                "(p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp'])::text "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='fence_research' AND "
                f"p.oid='fence_research.{signature}'::regprocedure;"
            )
            assert flags == "true:true", (signature, flags)

        scope = "a"*64
        owner = "b"*64
        claim = "c"*64
        jti = "d"*64
        model = "e"*64
        runtime = "f"*64
        first = "1"*64
        expiry = "clock_timestamp()+interval '40 seconds'"
        grant = (
            "SELECT fence_research.grant_scope("
            f"'{scope}','{owner}','{claim}',81,{expiry});"
        )
        assert psql("SET ROLE " + BROKER_ROLE + ";" + grant) == "1"
        denied(
            "SET ROLE " + WORKER_ROLE + ";" + grant,
            "permission denied for schema",
        )
        denied(
            "SET ROLE " + BROKER_ROLE + ";"
            "SELECT * FROM fence_research.scopes LIMIT 1;",
            "permission denied for table",
        )
        denied(
            "SET ROLE " + BROKER_ROLE + ";"
            "UPDATE fence_research.scopes SET state='revoked';",
            "permission denied for table",
        )
        reserve = (
            "SELECT fence_research.reserve("
            f"'{scope}',1,'{owner}','{claim}',81,'{jti}',{expiry},"
            f"2,'{first}','{model}','{runtime}');"
        )
        assert psql("SET ROLE " + BROKER_ROLE + ";" + reserve) == "1"
        revoke = (
            "SELECT fence_research.revoke_scope("
            f"'{scope}',1,'{owner}','{claim}');"
        )
        assert psql("SET ROLE " + BROKER_ROLE + ";" + revoke) == "t"
        denied("SET ROLE " + BROKER_ROLE + ";" + reserve, "fence stale")

        # Newly created routines do not reopen PUBLIC or broker EXECUTE.
        psql(
            "SET ROLE " + OWNER_ROLE + ";"
            "CREATE FUNCTION fence_research.future_acl_probe() RETURNS int "
            "LANGUAGE SQL AS $$ SELECT 1 $$;"
        )
        for role in (BROKER_ROLE, WORKER_ROLE):
            acl = psql(
                "SELECT has_function_privilege("
                f"'{role}','fence_research.future_acl_probe()','EXECUTE');"
            )
            assert acl == "f", (role, acl)

        # Deliberate counterexample: database grants alone do not authenticate
        # the owner/claim parameter supplied by a broker. This is NOT a GO.
        bogus = (
            "SELECT fence_research.grant_scope("
            f"'{('8'*64)}','{('9'*64)}','{('7'*64)}',81,{expiry});"
        )
        assert psql("SET ROLE " + BROKER_ROLE + ";" + bogus) == "1"

        return {
            "decision": "PASS_RESTRICTED_SQL_ACL_ONLY_NOT_ADMISSION",
            "schema_sha256": hashlib.sha256(schema).hexdigest(),
            "image": IMAGE, "image_sha256": imageid,
            "network": "none", "published_ports": 0,
            "owner_is_non_superuser": True,
            "broker_function_exec_only": True,
            "broker_direct_table_permissions": False,
            "worker_function_exec": False,
            "future_routine_exec_for_broker": False,
            "revoked_attempt_denied": True,
            "untrusted_worker_grant_denied": True,
            "broker_can_fabricate_owner_claim_without_external_verifier": True,
            "shared_epoch_admission_authorized": False,
            "provider_calls": 0,
            "container_removed_on_exit": True,
        }
    finally:
        if created:
            removed = subprocess.run(
                ["docker", "rm", "-f", CONTAINER],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if removed.returncode:
                raise RuntimeError("CRITICAL disposable DB container cleanup failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("PLAN_ONLY: --run required, no Docker or database effects")
        return
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
