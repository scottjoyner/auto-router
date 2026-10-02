"""Canonical ``${VAR:-default}`` expansion for every configuration reader.

Three modules previously carried their own expander with divergent regular
expressions:

* ``config._expand_env``          ``\\$\\{([^}:]+)(?::-([^}]*))?\\}``
* ``offline_guard._expand_env``   ``\\$\\{([^}:]+)(?::-([^}]*))?\\}``
* ``fleet_task_dispatcher._expand_env``  ``\\$\\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\\}``

The same provider YAML could therefore expand differently depending on which
reader touched it. This module is the single implementation; the three callers
import it rather than redefining it.

Semantics are pinned by ``tests/test_env_expansion.py``:

* Names must be shell identifiers (``[A-Za-z_][A-Za-z0-9_]*``). This is the
  stricter of the three original patterns, so unifying on it can only *narrow*
  what gets substituted, never widen it. Every ``${...}`` token in ``config/``,
  ``archive/``, and ``tests/`` uses an identifier, so no committed configuration
  changes behaviour.
* An unset variable expands to its ``:-`` default, or to the empty string when no
  default is given.
* Substitution recurses into lists and dicts. Non-string scalars pass through
  unchanged, matching the ``config`` and ``offline_guard`` behaviour.
* ``fleet_task_dispatcher`` previously expanded strings only; its single call
  site passes ``str(row.get("base_url") or "")``, so recursion never applies.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def expand_env(value: Any, env: Mapping[str, str] | None = None) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in *value*.

    ``env`` defaults to the process environment. Pass an explicit mapping to make
    expansion deterministic — ``offline_guard`` does this so strict-offline
    validation can be exercised against a fixed environment.
    """

    source: Mapping[str, str] = os.environ if env is None else env

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2) or ""
        return str(source.get(name, default))

    if isinstance(value, str):
        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [expand_env(item, source) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item, source) for key, item in value.items()}
    return value
