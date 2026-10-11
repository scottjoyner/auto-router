"""P0 #37: optional single PostgreSQL authority for router root/attempt identity.

This is a *reservation authority*, NOT an IdP, model-execution witness,
encrypted trace-custody service, or offline multi-master consensus. All router
workers must point at the SAME independently provisioned PostgreSQL primary.
A disconnected router must DENY new bound attempts, never write local forks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from auto_router.user_root_ledger import RootOwnerConflict, RootOwnerStorageError

_AUTHORITY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{3,63}$")
_SCHEMA = "p0_verified_user_owner_v1"
_DDL = (
    f"CREATE SCHEMA {_SCHEMA}",
    f"""CREATE TABLE {_SCHEMA}.authority (
            singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
            authority_id TEXT NOT NULL,
            key_check TEXT NOT NULL
        )""",
    f"""CREATE TABLE {_SCHEMA}.root_owner (
            root_hash TEXT PRIMARY KEY,
            owner_hash TEXT NOT NULL,
            model_hash TEXT NOT NULL
        )""",
    f"""CREATE TABLE {_SCHEMA}.attempt_owner (
            attempt_hash TEXT PRIMARY KEY,
            root_hash TEXT NOT NULL REFERENCES {_SCHEMA}.root_owner(root_hash),
            signer_jti_hash TEXT NOT NULL UNIQUE,
            token_hash TEXT NOT NULL
        )""",
)


class PostgresRootOwnerAuthority:
    """Immutable per-root authority with PostgreSQL primary-key serialization.

    For every request, a separate transaction attempts to insert the root
    and attempt identities. PostgreSQL unique constraints serialize races even
    across hosts. No caller-supplied claim/tenant/token is persisted in clear.
    """

    def __init__(
        self,
        dsn: str,
        secret: bytes,
        authority_id: str,
        *,
        bootstrap: bool = False,
        require_tls: bool = True,
    ) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise RootOwnerStorageError("p0 authority connection configuration missing")
        if not isinstance(secret, bytes) or len(secret) != 32:
            raise RootOwnerStorageError("p0 authority requires separate 32-byte HMAC key")
        if not isinstance(authority_id, str) or not _AUTHORITY_RE.fullmatch(authority_id):
            raise RootOwnerStorageError("p0 authority ID is missing or invalid")
        self._dsn = dsn
        self._secret = secret
        self.authority_id = authority_id
        self.require_tls = require_tls
        if bootstrap:
            self._bootstrap()
        else:
            self._verify()

    def _hash(self, purpose: str, *pieces: str) -> str:
        payload = json.dumps(
            [purpose, *pieces], separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return hmac.new(
            self._secret, b"p0-root-owner-v1\x00" + payload, hashlib.sha256
        ).hexdigest()

    def _connect(self):
        try:
            import psycopg
            connection = psycopg.connect(
                self._dsn,
                connect_timeout=3,
                options=(
                    "-c statement_timeout=3500 "
                    "-c lock_timeout=2000 "
                    "-c idle_in_transaction_session_timeout=5000"
                ),
            )
            # The loopback disposable experiment can opt out of TLS only by
            # passing require_tls=False directly to its test constructor.
            if self.require_tls and not bool(connection.pgconn.ssl_in_use):
                connection.close()
                raise RootOwnerStorageError("p0 authority requires verified TLS")
            return connection
        except RootOwnerStorageError:
            raise
        except Exception as exc:
            raise RootOwnerStorageError("p0 owner authority not reachable") from exc

    def _check_identity(self, cur) -> None:
        row = cur.execute(
            f"SELECT authority_id, key_check FROM {_SCHEMA}.authority WHERE singleton=1"
        ).fetchone()
        if (
            row is None
            or row[0] != self.authority_id
            or not isinstance(row[1], str)
            or not hmac.compare_digest(
                row[1], self._hash("ledger-key-check", "version-1")
            )
        ):
            raise RootOwnerStorageError("p0 owner authority identity/key mismatch")

    def _bootstrap(self) -> None:
        """Test-only offline provisioning, never invoked by live installer.

        Requires a NEW database: CREATE SCHEMA deliberately errors if an
        authority already exists. Never deletes, migrates, or resets one.
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for ddl in _DDL:
                        cur.execute(ddl)
                    cur.execute(
                        f"INSERT INTO {_SCHEMA}.authority "
                        "(singleton, authority_id, key_check) VALUES (1,%s,%s)",
                        (self.authority_id, self._hash("ledger-key-check", "version-1")),
                    )
        except RootOwnerStorageError:
            raise
        except Exception as exc:
            raise RootOwnerStorageError("p0 authority offline provisioning refused") from exc

    def _verify(self) -> None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    self._check_identity(cur)
        except RootOwnerStorageError:
            raise
        except Exception as exc:
            raise RootOwnerStorageError("p0 owner authority unavailable at startup") from exc

    def reserve(self, claims: dict[str, Any], token: str) -> bool:
        """Atomic shared-primary claim; an already committed token is rejected.

        Returns True for a new attempt and False ONLY for exact same signed
        attempt. Middleware refuses to dispatch an exact duplicate either way.
        """
        root_hash = self._hash("root", claims["root_request_id"])
        owner_hash = self._hash(
            "owner", claims["issuer"], claims["tenant"], claims["subject"]
        )
        model_hash = self._hash("model", claims["model_handle"])
        attempt_hash = self._hash("attempt", claims["attempt_id"])
        jti_hash = self._hash("jti", claims["jti"])
        token_hash = self._hash("signed-jwt", token)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    self._check_identity(cur)
                    # The unique root constraint makes conflicting writers on
                    # separate processes/hosts wait for the winning transaction.
                    cur.execute(
                        f"INSERT INTO {_SCHEMA}.root_owner "
                        "(root_hash, owner_hash, model_hash) VALUES (%s,%s,%s) "
                        "ON CONFLICT (root_hash) DO NOTHING",
                        (root_hash, owner_hash, model_hash),
                    )
                    owner = cur.execute(
                        f"SELECT owner_hash, model_hash FROM {_SCHEMA}.root_owner "
                        "WHERE root_hash=%s", (root_hash,)
                    ).fetchone()
                    if owner != (owner_hash, model_hash):
                        raise RootOwnerConflict("delegated root owner or model conflict")

                    # Same JTI cannot authorize a *different* attempt.
                    jti = cur.execute(
                        f"SELECT attempt_hash FROM {_SCHEMA}.attempt_owner "
                        "WHERE signer_jti_hash=%s", (jti_hash,)
                    ).fetchone()
                    if jti is not None and jti[0] != attempt_hash:
                        raise RootOwnerConflict("delegated signing jti was already reserved")

                    cur.execute(
                        f"INSERT INTO {_SCHEMA}.attempt_owner "
                        "(attempt_hash,root_hash,signer_jti_hash,token_hash) "
                        "VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        (attempt_hash, root_hash, jti_hash, token_hash),
                    )
                    created = bool(cur.rowcount)
                    attempt = cur.execute(
                        f"SELECT root_hash,signer_jti_hash,token_hash "
                        f"FROM {_SCHEMA}.attempt_owner WHERE attempt_hash=%s",
                        (attempt_hash,),
                    ).fetchone()
                    if attempt != (root_hash, jti_hash, token_hash):
                        raise RootOwnerConflict("delegated attempt was already reserved")
                    return created
        except RootOwnerConflict:
            raise
        except RootOwnerStorageError:
            raise
        except Exception as exc:
            raise RootOwnerStorageError("p0 shared owner authority unavailable") from exc


def read_authority_dsn_file(path: str) -> str:
    """A libpq conninfo file: dedicated credentials, verify-full TLS.

    Test constructors can explicitly disable TLS against a loopback
    disposable Postgres instance; production install must not.
    """
    import os
    import stat

    if not isinstance(path, str) or not path.startswith("/"):
        raise RootOwnerStorageError("p0 authority DSN file must be absolute")
    file = Path(path)
    try:
        meta = file.lstat()
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.getuid()
            or meta.st_mode & 0o077
        ):
            raise RootOwnerStorageError("p0 authority DSN file not private")
        data = file.read_text("utf-8").strip()
        import psycopg
        params = psycopg.conninfo.conninfo_to_dict(data)
    except RootOwnerStorageError:
        raise
    except Exception as exc:
        raise RootOwnerStorageError("p0 authority DSN file unavailable") from exc
    if (
        params.get("sslmode") != "verify-full"
        or not params.get("sslrootcert")
        or not params.get("host")
        or params.get("host", "").startswith("/")
    ):
        raise RootOwnerStorageError("p0 authority connection must pin verified TLS")
    return data
