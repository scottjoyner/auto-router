"""Synthetic physical xwing stream, gated by atomic PostgreSQL accepted-chunk ACK.

Caller controls a trusted disposable PG authority and starts this on xwing
through already verified pinned SSH. No model call, client HTTP connection,
real identity, remote filesystem change or persistence.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import select
import shlex
import subprocess
import time

import qualify_twohost as trial

ROOT=Path(__file__).resolve().parent


def _code(scope: str, epoch: int, request: int) -> str:
    pg=(ROOT/"pgwire_client.py").read_text()
    gate=(ROOT/"worker_effect_gate.py").read_text()
    # Both modules declare future imports. This physical probe executes only
    # preinspected local source text in a short-lived remote Python process.
    gate=gate.replace("from __future__ import annotations\n","")
    sql_pattern=(
        "SELECT fence_research.accept_chunk("
        f"'{scope}',{epoch},'{trial.HASH['owner']}','{trial.HASH['claim']}',81,"
        f"'{request:064x}',{{sequence}},'{{digest}}');"
    )
    return pg+"\n"+gate+"\n"+f'''
import json,sys
def check(sequence,digest):
    result=query({sql_pattern!r}.format(sequence=sequence,digest=digest),port=55539,timeout_s=3)
    return result==[[str(sequence)]]
was_cancelled=[]
worker=GuardedSyntheticStream(accept_chunk=check,cancel_model=lambda:was_cancelled.append(True))
try:
    worker.emit(b"synthetic-first-chunk")
    print(json.dumps({{"event":"released","sequence":1,"accepted":worker.accepted}}),flush=True)
    control=sys.stdin.readline().strip()
    if control!="NEXT":
        worker.stop()
        print(json.dumps({{"event":"blocked","reason":"control-channel-lost","cancelled":worker.cancelled}}),flush=True)
    else:
        try:
            worker.emit(b"synthetic-second-chunk")
            print(json.dumps({{"event":"released","sequence":2,"accepted":worker.accepted}}),flush=True)
        except WorkerEffectDenied:
            print(json.dumps({{"event":"blocked","reason":"fenced-or-unreachable",
                              "cancelled":worker.cancelled,"accepted":worker.accepted,
                              "cancel_calls":len(was_cancelled)}}),flush=True)
except WorkerEffectDenied:
    print(json.dumps({{"event":"blocked-before-first","cancelled":worker.cancelled}}),flush=True)
'''


def _line(process: subprocess.Popen, *, timeout: float=8.0) -> dict:
    if process.stdout is None:
        raise RuntimeError("remote physical worker stdout absent")
    descriptors,_,_=select.select([process.stdout],[],[],timeout)
    if not descriptors:
        raise RuntimeError("synthetic xwing worker event timed out")
    line=process.stdout.readline()
    if not line:
        raise RuntimeError("physical worker disconnected before producing event")
    try:
        data=json.loads(line)
    except ValueError as exc:
        raise RuntimeError("invalid physical worker observation") from exc
    if not isinstance(data,dict):
        raise RuntimeError("nonobject physical worker observation")
    return data


def start(scope: str, epoch: int, request: int) -> tuple[subprocess.Popen,dict]:
    source=_code(scope,epoch,request)
    encoded=base64.b64encode(source.encode()).decode()
    interpreter="import base64;exec(base64.b64decode("+repr(encoded)+"))"
    remote_command="python3 -u -c "+shlex.quote(interpreter)
    process=subprocess.Popen(
        trial.SSH+["xwing",remote_command],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,bufsize=1
    )
    try:
        first=_line(process)
        if first!={"event":"released","sequence":1,"accepted":1}:
            raise AssertionError("first worker chunk never accepted: "+repr(first))
        return process,first
    except Exception:
        process.kill()
        process.communicate(timeout=3)
        raise


def finish(process: subprocess.Popen) -> dict:
    try:
        if process.stdin is None:
            raise RuntimeError("remote worker stdin absent")
        process.stdin.write("NEXT\n")
        process.stdin.flush()
        event=_line(process)
        if event.get("event")!="blocked" or event.get("cancelled") is not True:
            raise AssertionError("unsafe remote effect accepted after loss: "+repr(event))
        if event.get("accepted")!=1 or event.get("cancel_calls")!=1:
            raise AssertionError("missing synthetic upstream cancellation: "+repr(event))
        if process.stdin:
            process.stdin.close()
        if process.wait(timeout=6)!=0:
            raise RuntimeError("remote worker ended nonzero after gating")
        return event
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=4)
        for stream in (process.stdout,process.stderr):
            if stream:stream.close()
