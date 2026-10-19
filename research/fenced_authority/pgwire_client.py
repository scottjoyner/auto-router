"""Minimal dependency-free PostgreSQL v3 simple-query client FOR DISPOSABLE TESTS.

No TLS or PostgreSQL authentication implementation: only use the temporary
trust-auth database behind pinned SSH loopback forwarding. Never use against
production PostgreSQL, or as a production SQL client.
"""
from __future__ import annotations

import socket
import struct


class PgwireError(RuntimeError):
    pass


def _exact(sock: socket.socket, count: int) -> bytes:
    if not 0 <= count <= 65536:
        raise PgwireError("PostgreSQL protocol response size rejected")
    chunks = []
    remaining = count
    while remaining:
        part = sock.recv(remaining)
        if not part:
            raise PgwireError("PostgreSQL protocol connection closed early")
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def _read_message(sock: socket.socket) -> tuple[bytes, bytes]:
    kind = _exact(sock, 1)
    (length,) = struct.unpack("!I", _exact(sock, 4))
    if not 4 <= length <= 65540:
        raise PgwireError("PostgreSQL invalid message length")
    return kind, _exact(sock, length - 4)


def _error_message(payload: bytes) -> str:
    fields = payload.split(b"\0")
    for field in fields:
        if field.startswith(b"M"):
            return field[1:400].decode("utf-8", "replace")
    return "database rejected request"


def query(sql: str, *, port: int = 55539, timeout_s: int = 8) -> list[list[str | None]]:
    """Run one synthetic SELECT statement over loopback on remote test host."""
    if port not in (55439, 55539) or type(sql) is not str or not 1 <= len(sql) <= 4096:
        raise PgwireError("unapproved test port or SQL size")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout_s) as sock:
        sock.settimeout(timeout_s)
        attrs = b"user\0postgres\0database\0postgres\0client_encoding\0UTF8\0\0"
        startup = struct.pack("!II", 8 + len(attrs), 196608) + attrs
        sock.sendall(startup)
        ready = False
        for _ in range(40):
            kind, data = _read_message(sock)
            if kind == b"R":
                if len(data) != 4 or struct.unpack("!I", data)[0] != 0:
                    raise PgwireError("disposable trust-auth only; credential challenge refused")
            elif kind == b"E":
                raise PgwireError(_error_message(data))
            elif kind == b"Z":
                ready = True
                break
        if not ready:
            raise PgwireError("PostgreSQL startup readiness missing")
        encoded = sql.encode("utf-8") + b"\0"
        sock.sendall(b"Q" + struct.pack("!I", len(encoded) + 4) + encoded)
        rows: list[list[str | None]] = []
        done = False
        for _ in range(128):
            kind, data = _read_message(sock)
            if kind == b"E":
                raise PgwireError(_error_message(data))
            if kind == b"D":
                if len(data) < 2:
                    raise PgwireError("short data row")
                (cols,) = struct.unpack("!H", data[:2])
                if cols > 16:
                    raise PgwireError("excess columns")
                pos = 2
                values: list[str | None] = []
                for _index in range(cols):
                    if pos + 4 > len(data):
                        raise PgwireError("truncated column")
                    (size,) = struct.unpack("!i", data[pos:pos+4])
                    pos += 4
                    if size == -1:
                        values.append(None)
                    elif 0 <= size <= 4096 and pos + size <= len(data):
                        values.append(data[pos:pos+size].decode("utf-8", "strict"))
                        pos += size
                    else:
                        raise PgwireError("invalid column length")
                if pos != len(data):
                    raise PgwireError("unexpected row trailing bytes")
                rows.append(values)
            if kind == b"Z":
                done = True
                break
        if not done:
            raise PgwireError("PostgreSQL query did not complete")
        return rows
