"""Hermetic guard tests for the *nonproduction* x1/xwing PG wire trial."""
from __future__ import annotations

from pathlib import Path
import socket
import struct
import threading
import pytest

ROOT = Path(__file__).resolve().parents[1]/"research/fenced_authority"
import sys
sys.path.insert(0,str(ROOT))
from pgwire_client import PgwireError, query, _exact, _read_message, _error_message


def test_disposable_twohost_runner_defaults_plan_only():
    source=(ROOT/"qualify_twohost.py").read_text()
    assert 'ap.add_argument("--run",action="store_true")' in source
    assert "PLAN_ONLY: explicit --run required" in source
    assert '"controller":"x1-370"' in source
    assert '"second_client":"xwing"' in source


def test_twohost_requires_verified_ssh_and_loopback_endpoints():
    source=(ROOT/"qualify_twohost.py").read_text()
    assert '"StrictHostKeyChecking=yes"' in source
    assert '"BatchMode=yes"' in source
    assert '127.0.0.1:{HOST_PORT}:5432' in source
    assert '127.0.0.1:{REMOTE_PORT}:127.0.0.1:{HOST_PORT}' in source
    assert 'if socket.gethostname()!="x1-370":' in source
    assert '"--network","host"' not in source
    assert '"--publish",f"127.0.0.1:{HOST_PORT}:5432"' in source


def test_temporary_port_forward_and_container_are_removed():
    source=(ROOT/"qualify_twohost.py").read_text()
    assert 'tunnel.terminate()' in source
    assert '["docker","rm","-f",CONTAINER]' in source
    assert '["docker","network","rm",NETWORK]' in source
    assert '"--memory","1024m"' in source
    assert '"--pids-limit","128"' in source


def test_no_packages_or_scripts_installed_on_xwing():
    source=(ROOT/"qualify_twohost.py").read_text()
    assert 'SSH+["xwing","python3","-"]' in source
    assert '"--network","host"' not in source
    assert "pip install" not in source
    assert "scp " not in source


@pytest.mark.parametrize("bad_port",[1,5432,55538,65535])
def test_raw_pg_client_rejects_other_ports(bad_port):
    with pytest.raises(PgwireError,match="unapproved"):
        query("SELECT 1;",port=bad_port)


def test_raw_pg_client_rejects_blank_or_giant_sql():
    with pytest.raises(PgwireError):
        query("",port=55539)
    with pytest.raises(PgwireError):
        query(" " * 4097,port=55439)


def test_protocol_reads_exact_framing():
    left,right=socket.socketpair()
    try:
        left.sendall(b"R"+struct.pack("!I",8)+struct.pack("!I",0))
        kind,payload=_read_message(right)
        assert (kind,payload)==(b"R",struct.pack("!I",0))
    finally:left.close();right.close()


def test_protocol_rejects_truncated_messages():
    left,right=socket.socketpair()
    try:
        left.sendall(b"R"+struct.pack("!I",8)+b"\x00")
        left.close()
        with pytest.raises(PgwireError,match="closed early"):
            _read_message(right)
    finally:right.close()


def test_protocol_rejects_oversized_messages():
    left,right=socket.socketpair()
    try:
        left.sendall(b"D"+struct.pack("!I",100000))
        with pytest.raises(PgwireError,match="invalid message length"):
            _read_message(right)
    finally:left.close();right.close()


def test_pg_client_roundtrip_over_mocked_socketpair(monkeypatch):
    import pgwire_client as pg
    local,server=socket.socketpair()
    monkeypatch.setattr(pg.socket,"create_connection",
                        lambda *_args,**_kwargs:local)
    exceptions=[]
    seen=[]
    def fake_pg():
        try:
            l=struct.unpack("!I",_exact(server,4))[0]
            request=_exact(server,l-4)
            assert request[:4]==struct.pack("!I",196608)
            assert b"user\0postgres\0" in request
            server.sendall(
                b"R"+struct.pack("!I",8)+struct.pack("!I",0)
                +b"Z"+struct.pack("!I",5)+b"I"
            )
            kind,payload=_read_message(server)
            assert kind==b"Q"
            seen.append(payload)
            data=b"\x00\x01"+struct.pack("!i",1)+b"1"
            server.sendall(
                b"D"+struct.pack("!I",4+len(data))+data
                +b"Z"+struct.pack("!I",5)+b"I"
            )
        except Exception as exc: exceptions.append(str(exc))
        finally:server.close()
    thread=threading.Thread(target=fake_pg,daemon=True)
    thread.start()
    assert pg.query("SELECT 1;",port=55539)==[["1"]]
    thread.join(timeout=2)
    assert not exceptions
    assert seen==[b"SELECT 1;\0"]


def test_pg_client_refuses_authentication_challenge(monkeypatch):
    import pgwire_client as pg
    local,server=socket.socketpair()
    monkeypatch.setattr(pg.socket,"create_connection",
                        lambda *_args,**_kwargs:local)
    def fake_pg():
        try:
            l=struct.unpack("!I",_exact(server,4))[0]
            _exact(server,l-4)
            server.sendall(b"R"+struct.pack("!I",8)+struct.pack("!I",5))
        finally:server.close()
    thread=threading.Thread(target=fake_pg,daemon=True)
    thread.start()
    with pytest.raises(PgwireError,match="credential challenge refused"):
        pg.query("SELECT 1;",port=55539)
    thread.join(timeout=2)


def test_pg_error_text_is_bounded():
    message=b"SERROR\0Mtoo_many_calls\0\0"
    assert _error_message(message)=="too_many_calls"
