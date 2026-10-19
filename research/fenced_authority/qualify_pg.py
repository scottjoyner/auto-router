#!/usr/bin/env python3
"""Operator-triggered, disposable single-primary PostgreSQL fence trials.

No ports, networking, mounts, production credentials or live router.
All clients are distinct psql processes sharing one container's primary.
Do not interpret this as cross-physical-host consensus/leader fencing.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import time

IMAGE="postgres:17-alpine"
CONTAINER="router-fence-pg-disposable-20261019"
ROOT=Path(__file__).resolve().parent
SCOPE="a"*64
OWNER_A="b"*64
OWNER_B="c"*64
CLAIM_A="d"*64
CLAIM_B="e"*64
MODEL="f"*64
RUNTIME="1"*64
JTI="2"*64
RECEIPT="3"*64


def shell(args:list[str],*,input_text:str|None=None,timeout:int=20)->str:
    result=subprocess.run(args,input=input_text,text=True,capture_output=True,
                          check=False,timeout=timeout)
    if result.returncode:
        raise RuntimeError("command failed rc="+str(result.returncode)+" :: "+
                           result.stderr[-450:].strip())
    return result.stdout.strip()


def psql(sql:str,*,deadline:int=20)->str:
    return shell(["docker","exec","-i",CONTAINER,"psql","-X","-v","ON_ERROR_STOP=1",
                  "-U","postgres","-d","postgres","-t","-A","-q"],
                 input_text=sql+"\n",timeout=deadline).strip()


def expect_fail(sql:str, *,message:str)->None:
    try:
        psql(sql)
    except RuntimeError as exc:
        if message not in str(exc):
            raise AssertionError("unexpected failure when expecting "+message+": "+str(exc)) from exc
        return
    raise AssertionError("unsafe SQL was allowed: "+message)


def query_single(stmt:str)->str:
    result=psql(stmt)
    if "\n" in result:
        raise AssertionError("unexpected multirow SQL output "+result[:100])
    return result


def run()->dict:
    if CONTAINER in shell(["docker","ps","-a","--format","{{.Names}}"],timeout=10).splitlines():
        raise RuntimeError("NO_GO disposable test name already in use")
    imageid=shell(["docker","image","inspect",IMAGE,"--format","{{.Id}}"],timeout=10)
    sql_bytes=(ROOT/"schema.sql").read_bytes()
    if not sql_bytes.startswith(b"-- EXPERIMENT ONLY"):
        raise RuntimeError("NO_GO schema header absent")
    schema_sha=hashlib.sha256(sql_bytes).hexdigest()
    began=time.monotonic()
    created=False
    try:
        shell(["docker","run","-d","--name",CONTAINER,"--hostname","localhost",
               "--network","none","--memory","1024m","--memory-swap","1024m",
               "--cpus","2","--pids-limit","128",
               "--security-opt","no-new-privileges:true",
               "-e","POSTGRES_HOST_AUTH_METHOD=trust",
               "-e","POSTGRES_INITDB_ARGS=--auth-local=trust",
               IMAGE],timeout=20)
        created=True
        ready=False
        for _ in range(28):
            chk=subprocess.run(["docker","exec",CONTAINER,"pg_isready","-U","postgres",
                                "-d","postgres"],capture_output=True,text=True,
                               timeout=4,check=False)
            if chk.returncode==0:
                ready=True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("disposable PostgreSQL never became ready")

        psql(sql_bytes.decode(),deadline=20)
        psql("CREATE ROLE fence_untrusted NOLOGIN;")
        untrusted=query_single(
            "SELECT has_function_privilege('fence_untrusted',"
            " 'fence_research.grant_scope(text,text,text,bigint,timestamptz)',"
            " 'EXECUTE');"
        )
        assert untrusted=="f", "unsafe untrusted function EXECUTE privilege"
        expect_fail(
            "SET ROLE fence_untrusted; SELECT fence_research.grant_scope("
            f"'{SCOPE}','{OWNER_A}','{CLAIM_A}',81,"
            "clock_timestamp()+interval '20 seconds');",
            message="permission denied for schema",
        )
        # External trusted broker MUST be the exclusive function invoker.
        # Native tests do not grant permissions or configure production ACLs.
        expires=query_single("SELECT (clock_timestamp() + interval '45 seconds')::text;")
        token_expires=query_single("SELECT (clock_timestamp() + interval '40 seconds')::text;")
        def grant(owner=OWNER_A,claim=CLAIM_A)->str:
            return query_single("SELECT fence_research.grant_scope("
                               f"'{SCOPE}','{owner}','{claim}',81,'{expires}'::timestamptz);")
        def reserve(req:int,*,epoch:int=1,owner=OWNER_A,claim=CLAIM_A,
                    jti=JTI,maximum:int=2)->str:
            request=f"{req:064x}"
            return query_single("SELECT fence_research.reserve("
                                f"'{SCOPE}',{epoch},'{owner}','{claim}',81,"
                                f"'{jti}','{token_expires}'::timestamptz,{maximum},"
                                f"'{request}','{MODEL}','{RUNTIME}');")
        def terminal(req:int,status="completed",epoch=1,receipt=RECEIPT)->str:
            return query_single("SELECT fence_research.commit_terminal("
                                f"'{SCOPE}',{epoch},'{OWNER_A}','{CLAIM_A}',"
                                f"'{req:064x}','{MODEL}','{RUNTIME}',"
                                f"'{status}','{receipt}');")

        assert grant()=="1"
        expect_fail(
            "SELECT fence_research.grant_scope("
            f"'{SCOPE}','{OWNER_B}','{CLAIM_B}',81,'{expires}'::timestamptz);",
            message="scope already active",
        )

        # Separate psql client processes contend for one signed two-attempt
        # budget; ALL target the same database primary. No client retries
        # after grant loss or expiry.
        def contender(i:int)->tuple[str,str]:
            try:return ("admitted",reserve(i))
            except RuntimeError as exc:
                if "budget exhausted" in str(exc):return ("denied","exhausted")
                raise
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(contender,range(1,7)))
        admitted=[x for x in results if x[0]=="admitted"]
        rejected=[x for x in results if x[0]=="denied"]
        if sorted(int(x[1]) for x in admitted)!=[1,2] or len(rejected)!=4:
            raise AssertionError("shared attempt budget over-admitted "+str(results))
        request_ids=[i for i,x in enumerate(results,start=1) if x[0]=="admitted"]
        first=request_ids[0]
        assert terminal(first)=="recorded"
        assert terminal(first)=="duplicate"
        expect_fail(
            "SELECT fence_research.commit_terminal("
            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}',"
            f"'{first:064x}','{MODEL}','{RUNTIME}','failed','{RECEIPT}');",
            message="conflicting immutable terminal",
        )
        attempts=query_single(f"SELECT consumed FROM fence_research.attempts WHERE jti_hash='{JTI}';")
        assert attempts=="2"
        assert query_single("SELECT fence_research.revoke_scope("
                            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}');")=="t"
        expect_fail(
            "SELECT fence_research.revoke_scope("
            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}');",
            message="wrong epoch/owner/claim",
        )
        expect_fail(
            "SELECT fence_research.reserve("
            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}',81,"
            f"'{JTI}','{token_expires}'::timestamptz,2,"
            f"'{7:064x}','{MODEL}','{RUNTIME}');",
            message="fence stale, revoked",
        )
        expect_fail(
            "SELECT fence_research.commit_terminal("
            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}',"
            f"'{request_ids[1]:064x}','{MODEL}','{RUNTIME}','completed','{RECEIPT}');",
            message="terminal rejected",
        )
        assert grant()=="2"
        expect_fail(
            "SELECT fence_research.reserve("
            f"'{SCOPE}',1,'{OWNER_A}','{CLAIM_A}',81,"
            f"'{JTI}','{token_expires}'::timestamptz,2,"
            f"'{8:064x}','{MODEL}','{RUNTIME}');",
            message="fence stale, revoked",
        )
        # New epoch and JTI may reserve; old JTI remains exhausted across
        # revocation and cannot be reused to cheat attempts.
        fresh_jti="4"*64
        assert reserve(9,epoch=2,jti=fresh_jti,maximum=1)=="1"
        expect_fail(
            "SELECT fence_research.reserve("
            f"'{SCOPE}',2,'{OWNER_B}','{CLAIM_A}',81,"
            f"'{fresh_jti}','{token_expires}'::timestamptz,1,"
            f"'{10:064x}','{MODEL}','{RUNTIME}');",
            message="fence stale, revoked",
        )
        assert query_single(f"SELECT count(*) FROM fence_research.executions WHERE scope_hash='{SCOPE}';")=="3"
        assert query_single(f"SELECT epoch FROM fence_research.scopes WHERE scope_hash='{SCOPE}';")=="2"

        # JTI was used only once under a three-attempt budget, but MUST NOT
        # survive replacement of its scope epoch. Epoch binding is critical.
        second_scope="5"*64
        second_jti="6"*64
        assert query_single(
            "SELECT fence_research.grant_scope("
            f"'{second_scope}','{OWNER_A}','{CLAIM_A}',81,"
            f"'{expires}'::timestamptz);"
        )=="1"
        first_req=f"{21:064x}"
        assert query_single(
            "SELECT fence_research.reserve("
            f"'{second_scope}',1,'{OWNER_A}','{CLAIM_A}',81,'{second_jti}',"
            f"'{token_expires}'::timestamptz,3,'{first_req}',"
            f"'{MODEL}','{RUNTIME}');"
        )=="1"
        assert query_single(
            "SELECT fence_research.revoke_scope("
            f"'{second_scope}',1,'{OWNER_A}','{CLAIM_A}');"
        )=="t"
        assert query_single(
            "SELECT fence_research.grant_scope("
            f"'{second_scope}','{OWNER_A}','{CLAIM_A}',81,"
            f"'{expires}'::timestamptz);"
        )=="2"
        expect_fail(
            "SELECT fence_research.reserve("
            f"'{second_scope}',2,'{OWNER_A}','{CLAIM_A}',81,'{second_jti}',"
            f"'{token_expires}'::timestamptz,3,'{22:064x}',"
            f"'{MODEL}','{RUNTIME}');",
            message="signed JTI reused under another binding",
        )

        # Two request IDs MUST NOT share a terminal receipt digest even
        # when signed upstream data are otherwise well formed.
        assert reserve(23,epoch=2,jti="7"*64,maximum=1)=="1"
        assert terminal(23,epoch=2,receipt="8"*64)=="recorded"
        expect_fail(
            "SELECT fence_research.commit_terminal("
            f"'{SCOPE}',2,'{OWNER_A}','{CLAIM_A}',"
            f"'{9:064x}','{MODEL}','{RUNTIME}','completed','{'8'*64}');",
            message="unique_verified_receipt_digest",
        )
        # Race two independent clients to grant the same never-seen scope.
        # Exactly one can become active at the database primary.
        race_scope="9"*64
        def racing_grant(owner:str, claim:str)->tuple[str,str]:
            try:
                result=query_single(
                    "SELECT fence_research.grant_scope("
                    f"'{race_scope}','{owner}','{claim}',81,"
                    f"'{expires}'::timestamptz);"
                )
                return ("won",result)
            except RuntimeError as exc:
                if "scope already active" in str(exc):
                    return ("denied","active")
                raise
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(racing_grant,OWNER_A,CLAIM_A),
                     pool.submit(racing_grant,OWNER_B,CLAIM_B)]
            race=[x.result(timeout=20) for x in futures]
        if len([x for x in race if x[0]=="won"])!=1 or len([
            x for x in race if x[0]=="denied"
        ])!=1:
            raise AssertionError("competing scope grants both succeeded or both failed")
        if query_single(
            f"SELECT count(*) FROM fence_research.scopes WHERE scope_hash='{race_scope}'"
            " AND epoch=1 AND state='active';"
        )!="1":
            raise AssertionError("unexpected competing grant scope state")

        return {
            "decision":"PASS_SINGLE_PRIMARY_MECHANISM_NOT_PRODUCTION",
            "postgres_image":IMAGE,"image_sha256":imageid,"schema_sha256":schema_sha,
            "network":"none","ports_published":0,
            "contending_client_processes":6,"simultaneous_grant_contenders":2,
            "single_grant_winner":1,"signed_attempt_budget":2,
            "accepted_attempts":2,"denied_over_budget":4,
            "duplicate_terminal":"idempotent",
            "conflicting_terminal":"denied",
            "revoked_epoch_late_terminal":"denied",
            "unspent_old_epoch_jti_reuse":"denied",
            "untrusted_sql_role_execute":"denied",
            "replacement_epoch":2,
            "no_cross_host_consensus_proven":True,
            "elapsed_s":round(time.monotonic()-began,2),
            "container_removed_on_exit":True,
        }
    finally:
        if created:
            cleanup=subprocess.run(["docker","rm","-f",CONTAINER],text=True,
                                   capture_output=True,timeout=14,check=False)
            if cleanup.returncode:
                raise RuntimeError("CRITICAL disposable PG cleanup failed: "+
                                   cleanup.stderr[-240:])


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not args.run:
        print("PLAN_ONLY: no container started; explicit --run required")
        return
    result=run()
    rendered=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if args.output:
        if not args.output.is_absolute() or args.output.is_symlink():
            raise RuntimeError("NO_GO output destination must be absolute nonsymlink")
        import os
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as handle:
            handle.write(rendered)
    print(rendered,end="")


if __name__=="__main__":main()
