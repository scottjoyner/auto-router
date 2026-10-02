"""Canonical SQLite database-URL to filesystem-path resolution.

Six persistence roots each carried a byte-identical ``_path_from_url``:

``ledger``, ``service_store``, ``memory_store``, ``signal_registry``,
``model_registry``, ``event_outbox``.

A single-URL fix previously had to touch six sites. This module is the single
implementation; each root imports ``sqlite_path`` instead of defining a method.

Accepted forms (unchanged, and pinned by ``tests/test_sqlite_path.py``):

===================================  ======================================
``sqlite:///./rel/path.sqlite3``     ``Path("rel/path.sqlite3")`` — cwd-relative
``sqlite:////abs/path.sqlite3``      ``Path("/abs/path.sqlite3")`` — absolute
``sqlite:///other.sqlite3``          ``Path("other.sqlite3")``
anything else                        passed to ``Path`` verbatim
===================================  ======================================

``Settings.latency_cache_path`` is deliberately *not* folded in here: it derives a
sibling filename next to the resolved database file and has its own
absolute-vs-cwd-relative handling, so sharing it would change behaviour rather
than deduplicate it.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def sqlite_path(database_url: str) -> Path:
    """Resolve a SQLite database URL to the filesystem path it addresses."""

    if database_url.startswith("sqlite:///./"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite:////"):
        return Path(urlparse(database_url).path)
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    return Path(database_url)
