"""Experimental immutable owner/root/attempt reservations for P0 issue #37.

NOT production trace custody or provider execution attestation. A separate,
operator-configured SQLite DB stores only HMAC-derived opaque identity hashes.
An independently provisioned secret must be retained across process restarts.
No expiry/pruning: retention and revocation need a separately approved policy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote


class RootOwnerConflict(ValueError):
    """Valid signed claims attempted to reassign a previously reserved identity."""


class RootOwnerStorageError(RuntimeError):
    """No permission to proceed when persistent root identity is unavailable."""


def _canonical(parts: list[str]) -> bytes:
    return json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class RootOwnerLedger:
    """Append-only, serializable bindings for one trusted router DB.

    A shared durable SQLite database is required for all router workers.
    A second node's independent DB is NOT equivalent to cluster-wide ownership.
    """

    def __init__(self, path: Path, secret: bytes, *, bootstrap: bool = False) -> None:
        if not path.is_absolute() or path.name in ("", ".", ".."):
            raise RootOwnerStorageError("root ledger requires an absolute file path")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise RootOwnerStorageError("root ledger requires a separate secret")
        self.path = path
        self.secret = secret
        self._identity: tuple[int, int] | None = None
        self._initialize(bootstrap=bootstrap)

    def _digest(self, kind: str, *parts: str) -> str:
        material = b"p0-root-owner-v1\x00" + _canonical([kind, *parts])
        return hmac.new(self.secret, material, hashlib.sha256).hexdigest()

    def _secure_db_stat(self) -> os.stat_result:
        try:
            attributes = self.path.lstat()
        except OSError as exc:
            raise RootOwnerStorageError("root ledger file missing") from exc
        if not stat.S_ISREG(attributes.st_mode):
            raise RootOwnerStorageError("root ledger must be a regular file")
        if attributes.st_uid != os.getuid() or attributes.st_mode & 0o077:
            raise RootOwnerStorageError("root ledger has unsafe ownership or permissions")
        if self._identity is not None and (
            attributes.st_dev, attributes.st_ino
        ) != self._identity:
            raise RootOwnerStorageError("root ledger storage identity changed")
        return attributes

    def _connect(self) -> sqlite3.Connection:
        # SQLite's mode=rw refuses to recreate a missing store after a crash
        # or unmount. A changed inode must not bypass previously bound roots.
        self._secure_db_stat()
        try:
            uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
            connection = sqlite3.connect(uri, timeout=5, uri=True)
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.Error as exc:
            raise RootOwnerStorageError("root ledger connection unavailable") from exc

    def _initialize(self, *, bootstrap: bool) -> None:
        if self.path.is_symlink():
            raise RootOwnerStorageError("root ledger symlink is forbidden")
        try:
            directory = self.path.parent.stat()
            if not stat.S_ISDIR(directory.st_mode):
                raise RootOwnerStorageError("root ledger parent directory invalid")
            if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
                raise RootOwnerStorageError("root ledger parent directory is not private")
            # Provisioning is an explicit, one-time offline action. Runtime
            # initialization must NEVER recreate a lost or unmounted store.
            if bootstrap:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            elif not self.path.exists():
                raise RootOwnerStorageError("root ledger requires explicit provisioning")
            attributes = self._secure_db_stat()
            with closing(self._connect()) as connection, connection:
                connection.execute("PRAGMA journal_mode=WAL")
                if bootstrap:
                    connection.execute(
                        """CREATE TABLE IF NOT EXISTS p0_owner_key_check (
                               singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                               key_check TEXT NOT NULL
                           )"""
                    )
                    connection.execute(
                        """CREATE TABLE IF NOT EXISTS p0_root_owner (
                               root_hash TEXT PRIMARY KEY,
                               owner_hash TEXT NOT NULL,
                               model_hash TEXT NOT NULL
                           )"""
                    )
                    connection.execute(
                        """CREATE TABLE IF NOT EXISTS p0_attempt_owner (
                               attempt_hash TEXT PRIMARY KEY,
                               root_hash TEXT NOT NULL,
                               signer_jti_hash TEXT NOT NULL,
                               token_hash TEXT NOT NULL,
                               FOREIGN KEY (root_hash) REFERENCES p0_root_owner(root_hash)
                           )"""
                    )
                connection.execute("PRAGMA foreign_keys=ON")
                expected = self._digest("ledger-key-check", "version-1")
                row = connection.execute(
                    "SELECT key_check FROM p0_owner_key_check WHERE singleton=1"
                ).fetchone()
                if row is None:
                    # Normal runtime never initializes missing identity state,
                    # even if someone presents an empty replacement SQLite DB.
                    if not bootstrap:
                        raise RootOwnerStorageError("root ledger key metadata absent")
                    connection.execute(
                        "INSERT INTO p0_owner_key_check (singleton, key_check) VALUES (1, ?)",
                        (expected,),
                    )
                elif not isinstance(row[0], str) or not hmac.compare_digest(row[0], expected):
                    raise RootOwnerStorageError("root ledger key mismatch")
                connection.commit()
            self._identity = (attributes.st_dev, attributes.st_ino)
        except RootOwnerStorageError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise RootOwnerStorageError("root ledger initialization unavailable") from exc

    def reserve(self, claims: dict[str, Any], token: str) -> bool:
        """Atomic reservation, idempotent for *exactly* the same signed token.

        Distinct roots can never share an attempt UUID. One root is forever
        bound to one issuer/tenant/subject/model. No destructive updates/deletes.
        """
        root_hash = self._digest("root", claims["root_request_id"])
        attempt_hash = self._digest("attempt", claims["attempt_id"])
        owner_hash = self._digest(
            "owner", claims["issuer"], claims["tenant"], claims["subject"]
        )
        model_hash = self._digest("model", claims["model_handle"])
        signer_jti_hash = self._digest("jti", claims["jti"])
        token_hash = self._digest("signed-jwt", token)
        connection = self._connect()
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            observed_key = connection.execute(
                "SELECT key_check FROM p0_owner_key_check WHERE singleton=1"
            ).fetchone()
            expected = self._digest("ledger-key-check", "version-1")
            if (
                observed_key is None
                or not isinstance(observed_key[0], str)
                or not hmac.compare_digest(observed_key[0], expected)
            ):
                raise RootOwnerStorageError("root ledger key mismatch")

            root = connection.execute(
                "SELECT owner_hash, model_hash FROM p0_root_owner WHERE root_hash=?",
                (root_hash,),
            ).fetchone()
            if root is None:
                connection.execute(
                    "INSERT INTO p0_root_owner(root_hash, owner_hash, model_hash) "
                    "VALUES (?, ?, ?)",
                    (root_hash, owner_hash, model_hash),
                )
            elif root != (owner_hash, model_hash):
                raise RootOwnerConflict("delegated root owner or model conflict")

            attempt = connection.execute(
                "SELECT root_hash, signer_jti_hash, token_hash "
                "FROM p0_attempt_owner WHERE attempt_hash=?",
                (attempt_hash,),
            ).fetchone()
            if attempt is None:
                connection.execute(
                    "INSERT INTO p0_attempt_owner "
                    "(attempt_hash, root_hash, signer_jti_hash, token_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (attempt_hash, root_hash, signer_jti_hash, token_hash),
                )
                created = True
            elif attempt != (root_hash, signer_jti_hash, token_hash):
                raise RootOwnerConflict("delegated attempt was already reserved")
            else:
                created = False
            connection.commit()
            return created
        except (RootOwnerConflict, RootOwnerStorageError):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise RootOwnerStorageError("root ledger reservation unavailable") from exc
        finally:
            connection.close()


def read_pinned_root_secret(path: str) -> bytes:
    """Reject key material in env and weak file permissions."""
    if not path.startswith("/"):
        raise RootOwnerStorageError("root ledger requires absolute HMAC key file")
    file = Path(path)
    try:
        meta = file.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
            raise RootOwnerStorageError("root secret has unsafe permissions")
        if meta.st_uid != os.getuid():
            raise RootOwnerStorageError("root secret has unsafe owner")
        secret = file.read_bytes()
    except OSError as exc:
        raise RootOwnerStorageError("root secret unavailable") from exc
    if len(secret) != 32:
        raise RootOwnerStorageError("root secret must be exactly 32 bytes")
    return secret
