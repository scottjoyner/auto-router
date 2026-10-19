"""Fail-closed, owner-bound durable inference attempt reservations (opt-in only).

Persistent pre-dispatch record, NOT a terminal inference receipt. SQLite
synchronizes processes sharing one filesystem. It is NOT sufficient for
two hosts with independent disks or fencing/revocation without AssistX.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any, Mapping

from .owner_binding import OwnerBindingError, _identity


class OwnerAttemptStoreError(RuntimeError):
    """Storage cannot prove durable owner-scoped attempt state."""


class OwnerAttemptDenied(ValueError):
    """A token was reused too often or with a different signed claim."""


def _digest(label: str, values: list[str]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(label.encode("ascii") + b"\0" + encoded).hexdigest()


def _binding(claims: Mapping[str, Any]) -> tuple[str, str]:
    try:
        issuer, subject = _identity(claims, origin="signed token")
        names = []
        for key in ("task_id", "claim_id", "agent_id", "projection_generation"):
            val = claims.get(key)
            if type(val) not in (str, int) or not str(val).strip():
                raise OwnerAttemptDenied(f"missing signed execution binding {key}")
            names.append(str(val))
        jti = claims.get("jti")
        if type(jti) is not str or not 1 <= len(jti) <= 256:
            raise OwnerAttemptDenied("missing signed token JTI")
        return (_digest("owner-attempt-jti-v1", [jti]),
                _digest("owner-attempt-binding-v1", [issuer, subject, *names]))
    except OwnerBindingError as exc:
        raise OwnerAttemptDenied(str(exc)) from exc


def _private_parent(path: Path) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent.is_symlink() or not parent.is_dir():
        raise OwnerAttemptStoreError("attempt DB parent missing or symlinked")
    attrs = parent.stat()
    if attrs.st_uid != os.getuid() or attrs.st_mode & 0o077:
        raise OwnerAttemptStoreError("attempt DB parent must be privately owned 0700")
    if not path.is_absolute() or path.is_symlink() or path.name in ("", ".", ".."):
        raise OwnerAttemptStoreError("attempt DB must be an absolute regular path")


def _private_file(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            attrs = os.fstat(fd)
            if (not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid()
                or attrs.st_nlink != 1 or attrs.st_mode & 0o077):
                raise OwnerAttemptStoreError("attempt DB file has unsafe ownership or permissions")
        finally:
            os.close(fd)
    except OSError as exc:
        raise OwnerAttemptStoreError("attempt DB file cannot be securely opened") from exc


class DurableOwnerAttemptLedger:
    def __init__(self, path: str):
        self.path = Path(path)
        if (not path or path in (":memory:", "file::memory:") or
            not self.path.is_absolute()):
            raise OwnerAttemptStoreError("strict owner mode requires absolute file-backed SQLite")
        _private_parent(self.path)
        _private_file(self.path)
        try:
            with self._connect() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS signed_owner_attempts (
                        jti_hash TEXT PRIMARY KEY,
                        binding_hash TEXT NOT NULL,
                        token_expires_s INTEGER NOT NULL,
                        max_attempts INTEGER NOT NULL,
                        consumed INTEGER NOT NULL CHECK(consumed >= 0)
                    )
                """)
        except sqlite3.Error as exc:
            raise OwnerAttemptStoreError("attempt schema unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            return connection
        except sqlite3.Error as exc:
            raise OwnerAttemptStoreError("attempt DB connection unavailable") from exc

    def acquire(self, claims: Mapping[str, Any], *, now: int | None = None) -> int:
        jti_hash, binding_hash = _binding(claims)
        now_s = int(time.time()) if now is None else now
        if type(now_s) is not int or now_s <= 0:
            raise OwnerAttemptDenied("invalid attempt timestamp")
        expiration = claims.get("exp")
        maximum = claims.get("max_attempts")
        if (type(expiration) is not int or expiration <= now_s
            or expiration > now_s + 86400):
            raise OwnerAttemptDenied("attempt token expired or exceeds bounded validity")
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise OwnerAttemptDenied("signed attempt budget invalid")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT binding_hash,token_expires_s,max_attempts,consumed "
                    "FROM signed_owner_attempts WHERE jti_hash=?",
                    (jti_hash,),
                ).fetchone()
                if row:
                    if (row[0] != binding_hash or row[1] != expiration or row[2] != maximum):
                        raise OwnerAttemptDenied("token JTI reused with different owner or claim")
                    if row[3] >= maximum:
                        raise OwnerAttemptDenied("durable owner attempt budget exhausted")
                    attempt = row[3] + 1
                    db.execute("UPDATE signed_owner_attempts SET consumed=? WHERE jti_hash=?",
                               (attempt, jti_hash))
                else:
                    attempt = 1
                    db.execute(
                        "INSERT INTO signed_owner_attempts "
                        "(jti_hash,binding_hash,token_expires_s,max_attempts,consumed) "
                        "VALUES (?,?,?,?,1)",
                        (jti_hash, binding_hash, expiration, maximum),
                    )
                # Transaction commits and fsyncs before dispatch can proceed.
            return attempt
        except OwnerAttemptDenied:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise OwnerAttemptStoreError("unable to commit durable owner attempt") from exc

    def count(self, claims: Mapping[str, Any]) -> int:
        """Local test/diagnostic only; never emits user identifiers."""
        jti_hash, _ = _binding(claims)
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT consumed FROM signed_owner_attempts WHERE jti_hash=?",
                    (jti_hash,),
                ).fetchone()
                return int(row[0]) if row else 0
        except sqlite3.Error as exc:
            raise OwnerAttemptStoreError("attempt lookup unavailable") from exc


def acquire_strict_attempt(claims: Mapping[str, Any]) -> int:
    value = os.getenv("AUTO_ROUTER_OWNER_ATTEMPT_DB_PATH", "").strip()
    if not value:
        raise OwnerAttemptStoreError("strict owner mode requires configured durable attempt DB")
    return DurableOwnerAttemptLedger(value).acquire(claims)
