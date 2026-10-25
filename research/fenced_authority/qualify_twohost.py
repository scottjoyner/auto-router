#!/usr/bin/env python3
"""Research-only physical x1/xwing PostgreSQL single-primary contention trial.

Runs ONLY on x1-370. Creates an isolated Docker PostgreSQL instance whose
5432 is mapped to x1 loopback, then a temporary pinned-SSH reverse tunnel
to xwing loopback. The xwing Python process itself issues PostgreSQL v3
protocol messages. No xwing packages/files/services are installed.
No live database, auth token, model inference or production route involved.

This is physical CLIENT separation, NOT independent authority custody,
consensus or resource-side cancellation proof.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from pgwire_client import query, PgwireError

ROOT=Path(__file__).resolve().parent
CONTAINER="router-fence-pg-twohost-20261019"
NETWORK="router-fence-isolated-20261019"
IMAGE="postgres:17-alpine"
HOST_PORT=55439
REMOTE_PORT=55539
SSH=["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
     "-o","ConnectTimeout=5","-o","ConnectionAttempts=1"]
HASH={
    "scope_main":"a"*64,
    "scope_remote":"b"*64,
    "scope_race":"c"*64,
    "owner":"d"*64,
    "claim":"e"*64,
    "model":"f"*64,
    "runtime":"1"*64,
    "jti_main":"2"*64,
    "jti_remote":"3"*64,
    "jti_epoch2":"4"*64,
    "receipt":"5"*64,
}
SRC=(ROOT/"pgwire_client.py").read_text()


def run_cmd(args, *,payload=None, timeout=14, check=True):
    r=subprocess.run(args,input=payload,stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,text=True,timeout=timeout,check=False)
    if check and r.returncode:
        raise RuntimeError("subprocess failed: "+repr(args[:4])+" "+
                           (r.stderr or r.stdout)[-320:])
    return r


def already_in_use() -> None:
    for port in (HOST_PORT,):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1",port))==0:
                raise RuntimeError("NO_GO controller loopback test port in use")
    if CONTAINER in run_cmd(["docker","ps","-a","--format","{{.Names}}"]).stdout.splitlines():
        raise RuntimeError("NO_GO disposable container name collision")
    if NETWORK in run_cmd(["docker","network","ls","--format","{{.Name}}"]).stdout.splitlines():
        raise RuntimeError("NO_GO disposable Docker network already exists")
    inspect=run_cmd(SSH+["xwing","hostname"]).stdout.strip()
    if inspect!="xwing":
        raise RuntimeError("NO_GO expected pinned SSH host xwing")
    probe=run_cmd(SSH+["xwing","python3","-"],
                  payload="import socket\ns=socket.socket()\n"
                          "print(s.connect_ex(('127.0.0.1',55539)))\n"
                          "s.close()\n")
    if probe.stdout.strip()=="0":
        raise RuntimeError("NO_GO xwing loopback tunnel port already in use")


def local(sql: str)->str:
    r=run_cmd(["docker","exec","-i",CONTAINER,"psql","-X",
               "-v","ON_ERROR_STOP=1","-t","-A","-q","-U","postgres",
               "-d","postgres"],payload=sql+"\n",timeout=13,check=False)
    if r.returncode:
        return "DENIED:"+r.stderr[-260:].strip()
    return "OK:"+r.stdout.strip()


def remote(sql: str)->str:
    # Send source over existing authenticated SSH stdin. On xwing,
    # Python executes the SQL through xwing's own loopback tunnel socket.
    # No source is persisted on xwing.
    script=SRC+"\nimport json\ntry:\n"
    script+="    print(json.dumps({'ok':True,'rows':query("+repr(sql)+",port=55539)}))\n"
    script+="except Exception as e:\n"
    script+="    print(json.dumps({'ok':False,'error':str(e)[:240]}))\n"
    r=run_cmd(SSH+["xwing","python3","-"],payload=script,timeout=16,check=False)
    if r.returncode:
        raise RuntimeError("xwing remote PG client failed: "+r.stderr[-240:])
    try:
        obj=json.loads(r.stdout.strip())
    except (ValueError,TypeError) as exc:
        raise RuntimeError("xwing non-JSON wire client response: "+r.stdout[-250:]) from exc
    if obj.get("ok") is True:
        rows=obj.get("rows",[])
        return "OK:" + (rows[0][0] if rows and rows[0] else "")
    return "DENIED:" + str(obj.get("error","unknown"))


def must_ok(result:str, expected:str|None=None) -> str:
    if not result.startswith("OK:"):
        raise AssertionError("expected permitted operation: "+result)
    value=result[3:]
    if expected is not None and value!=expected:
        raise AssertionError("wrong allowed result "+repr(value)+" expected "+repr(expected))
    return value


def must_deny(result:str, fragment:str):
    if not result.startswith("DENIED:") or fragment not in result:
        raise AssertionError("unexpected rejection/acceptance "+result+" expected "+fragment)


def stmt_grant(scope:str,expiry:str) -> str:
    return ("SELECT fence_research.grant_scope("
            f"'{scope}','{HASH['owner']}','{HASH['claim']}',81,"
            f"'{expiry}'::timestamptz);")


def stmt_revoke(scope:str,epoch:int)->str:
    return ("SELECT fence_research.revoke_scope("
            f"'{scope}',{epoch},'{HASH['owner']}','{HASH['claim']}');")


def stmt_reserve(scope:str,epoch:int,jti:str,request:int,expiry:str,
                 budget:int=2)->str:
    return ("SELECT fence_research.reserve("
            f"'{scope}',{epoch},'{HASH['owner']}','{HASH['claim']}',81,"
            f"'{jti}','{expiry}'::timestamptz,{budget},"
            f"'{request:064x}','{HASH['model']}','{HASH['runtime']}');")


def stmt_terminal(scope:str,epoch:int,request:int,status="completed")->str:
    return ("SELECT fence_research.commit_terminal("
            f"'{scope}',{epoch},'{HASH['owner']}','{HASH['claim']}',"
            f"'{request:064x}','{HASH['model']}','{HASH['runtime']}',"
            f"'{status}','{HASH['receipt']}');")


def execute() -> dict:
    if socket.gethostname()!="x1-370":
        raise RuntimeError("NO_GO run physical twohost only from x1-370")
    already_in_use()
    if not (ROOT/"schema.sql").read_bytes().startswith(b"-- EXPERIMENT ONLY"):
        raise RuntimeError("NO_GO source not recognized as research schema")
    image=run_cmd(["docker","image","inspect",IMAGE,"--format","{{.Id}}"]).stdout.strip()
    created_network=False
    created_container=False
    tunnel=None
    start=time.monotonic()
    try:
        # Docker --internal disables published-port host access here. Use
        # an ephemeral user-defined bridge and bind the only published port
        # to 127.0.0.1. No production credentials or data enter the container.
        run_cmd(["docker","network","create","--driver","bridge",
                 "--label","owner=research-glm-pg-twohost",NETWORK],timeout=12)
        created_network=True
        run_cmd(["docker","run","-d","--name",CONTAINER,"--hostname","localhost",
                 "--network",NETWORK,"--publish",f"127.0.0.1:{HOST_PORT}:5432",
                 "--memory","1024m","--memory-swap","1024m","--cpus","2",
                 "--pids-limit","128","--security-opt","no-new-privileges:true",
                 "-e","POSTGRES_HOST_AUTH_METHOD=trust",
                 "-e","POSTGRES_INITDB_ARGS=--auth-local=trust",
                 IMAGE],timeout=20)
        created_container=True
        for _ in range(35):
            r=run_cmd(["docker","exec",CONTAINER,"pg_isready","-U","postgres"],
                      timeout=4,check=False)
            if r.returncode==0:break
            time.sleep(1)
        else:raise RuntimeError("disposable isolated PostgreSQL not ready")

        # Validate Docker localhost publishing before troubleshooting SSH.
        if query("SELECT 1;",port=HOST_PORT) != [["1"]]:
            raise RuntimeError("NO_GO controller localhost PG v3 transport unavailable")
        # Forward only 127.0.0.1 on both endpoints, SSH pinned to xwing.
        tunnel=subprocess.Popen(
            SSH+["-o","ExitOnForwardFailure=yes","-o","ServerAliveInterval=3",
                 "-o","ServerAliveCountMax=2","-N",
                 "-R",f"127.0.0.1:{REMOTE_PORT}:127.0.0.1:{HOST_PORT}","xwing"],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        last_probe=""
        for _ in range(16):
            if tunnel.poll() is not None:
                raise RuntimeError("SSH reverse forward failed: "+
                                   tunnel.stderr.read().decode(errors="replace")[-320:])
            last_probe=remote("SELECT 1;")
            if last_probe=="OK:1":break
            time.sleep(.5)
        else:
            raise RuntimeError("xwing PostgreSQL client tunnel never became ready: "
                               +last_probe[-240:])
        # Verify local and remote all access the same disposable PG primary.
        must_ok(local("SELECT current_database();"),"postgres")
        must_ok(remote("SELECT current_database();"),"postgres")
        schema=(ROOT/"schema.sql").read_text()
        must_ok(local(schema))
        expiry=must_ok(local("SELECT (clock_timestamp()+interval '45 seconds')::text;"))
        token_expiry=must_ok(local("SELECT (clock_timestamp()+interval '40 seconds')::text;"))
        must_ok(local(stmt_grant(HASH["scope_main"],expiry)),"1")

        def contender(i:int)->tuple[str,str]:
            statement=stmt_reserve(HASH["scope_main"],1,HASH["jti_main"],
                                   i,token_expiry,budget=2)
            return ("xwing" if i%2 else "x1",
                    remote(statement) if i%2 else local(statement))
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(contender,range(1,9)))
        admitted=[(host,result) for host,result in results if result.startswith("OK:")]
        refused=[(host,result) for host,result in results if result.startswith("DENIED:")]
        if sorted(int(x[1][3:]) for x in admitted)!=[1,2] or len(refused)!=6:
            raise AssertionError("multi-host shared attempt limit violated "+str(results))
        for _,denial in refused:
            must_deny(denial,"budget exhausted")
        # With a fresh second scope, prove an xwing client can grant and
        # reserve, then an x1 client participates in the same scope.
        must_ok(remote(stmt_grant(HASH["scope_remote"],expiry)),"1")
        must_ok(remote(stmt_reserve(HASH["scope_remote"],1,HASH["jti_remote"],
                                    100,token_expiry,budget=2)),"1")
        must_ok(local(stmt_reserve(HASH["scope_remote"],1,HASH["jti_remote"],
                                   101,token_expiry,budget=2)),"2")
        must_ok(remote(stmt_terminal(HASH["scope_remote"],1,101)),"recorded")
        must_ok(local(stmt_terminal(HASH["scope_remote"],1,101)),"duplicate")

        # Revoke from x1, prove xwing cannot commit a late attempt/terminal,
        # then regrant from xwing and prove an old JTI cannot cross epochs.
        must_ok(local(stmt_revoke(HASH["scope_remote"],1)),"t")
        must_deny(remote(stmt_terminal(HASH["scope_remote"],1,100)),
                  "terminal rejected by stale/revoked/expired fence")
        must_deny(remote(stmt_reserve(HASH["scope_remote"],1,HASH["jti_remote"],
                                      102,token_expiry,budget=2)),
                  "fence stale, revoked")
        must_ok(remote(stmt_grant(HASH["scope_remote"],expiry)),"2")
        must_deny(local(stmt_reserve(HASH["scope_remote"],2,HASH["jti_remote"],
                                     103,token_expiry,budget=2)),
                  "signed JTI reused under another binding")
        must_ok(remote(stmt_reserve(HASH["scope_remote"],2,HASH["jti_epoch2"],
                                    104,token_expiry,budget=1)),"1")
        must_deny(local(stmt_reserve(HASH["scope_remote"],1,HASH["jti_epoch2"],
                                     105,token_expiry,budget=1)),
                  "fence stale, revoked")

        # Competing grants from two *physical* clients on an empty scope.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(local,stmt_grant(HASH["scope_race"],expiry)),
                     pool.submit(remote,stmt_grant(HASH["scope_race"],expiry))]
            grants=[f.result(timeout=17) for f in futures]
        if len([x for x in grants if x=="OK:1"])!=1 or len([
            x for x in grants if x.startswith("DENIED:") and "already active" in x
        ])!=1:
            raise AssertionError("unsafe physical competing grants "+str(grants))

        # Partition simulation: revoke an existing xwing-executed epoch
        # while tunnel is connected, then remove the authority path from
        # xwing. x1 can regrant epoch 3; xwing cannot reach PostgreSQL.
        # This does NOT terminate model work already accepted on xwing.
        must_ok(local(stmt_revoke(HASH["scope_remote"],2)),"t")
        must_deny(remote(stmt_terminal(HASH["scope_remote"],2,104)),
                  "terminal rejected by stale/revoked/expired fence")
        must_ok(local(stmt_grant(HASH["scope_remote"],expiry)),"3")
        tunnel.terminate()
        tunnel.wait(timeout=5)
        failure=remote("SELECT 1;")
        if not failure.startswith("DENIED:") or not any(
            word in failure.lower() for word in ("refused","unavailable","timed out")
        ):
            raise AssertionError("xwing authority path remained available after tunnel loss: "
                                 +failure)
        must_ok(local(stmt_reserve(HASH["scope_remote"],3,"6"*64,
                                   106,token_expiry,budget=1)),"1")

        return {
            "decision":"PASS_TWO_PHYSICAL_CLIENTS_ONE_PRIMARY_NOT_CONSENSUS",
            "controller":"x1-370","second_client":"xwing",
            "second_client_exec":"xwing Python3 PostgreSQL wire over SSH reverse tunnel",
            "postgres_image_sha256":image,
            "schema_sha256":hashlib.sha256(schema.encode()).hexdigest(),
            "host_port_binding":"127.0.0.1:55439",
            "remote_port_binding":"127.0.0.1:55539",
            "docker_network_internal":False,"ephemeral_private_bridge":True,
            "public_ports":0,
            "attempt_contenders":len(results),"attempt_limit":2,
            "admitted":len(admitted),"denied":len(refused),
            "xwing_grant_and_reservation":"passed",
            "x1_shared_scope_reservation":"passed",
            "cross_host_exact_terminal_duplicate":"idempotent",
            "cross_host_revocation_late_receipt":"denied",
            "cross_host_old_jti_reuse":"denied",
            "physical_competing_grants_one_winner":"passed",
            "physical_ssh_tunnel_partition":"xwing_authority_unreachable",
            "post_partition_replacement_epoch":3,
            "revocation_while_remote_epoch_active":"late_terminal_denied",
            "no_worker_side_cancel_proven":True,
            "no_independent_administrative_custody":True,
            "elapsed_seconds":round(time.monotonic()-start,2),
            "ephemeral_resources_cleaned":True,
        }
    finally:
        if tunnel is not None:
            if tunnel.poll() is None:
                tunnel.terminate()
                try:tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=5)
            if tunnel.stderr:tunnel.stderr.close()
        if created_container:
            r=run_cmd(["docker","rm","-f",CONTAINER],timeout=13,check=False)
            if r.returncode:
                raise RuntimeError("CRITICAL disposable PG cleanup failed: "+r.stderr[-240:])
        if created_network:
            r=run_cmd(["docker","network","rm",NETWORK],timeout=13,check=False)
            if r.returncode:
                raise RuntimeError("CRITICAL disposable Docker network cleanup failed: "+r.stderr[-240:])


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--run",action="store_true")
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    if not args.run:
        print("PLAN_ONLY: explicit --run required; no network/tunnel/container started")
        return
    result=execute()
    body=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if args.output:
        target=args.output
        if not target.is_absolute() or target.is_symlink() or target.exists():
            raise RuntimeError("NO_GO output must be a new absolute nonsymlink path")
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as file:file.write(body)
    print(body,end="")


if __name__=="__main__":main()
