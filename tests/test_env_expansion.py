"""Contract tests for the shared ``${VAR:-default}`` expander.

``env_expansion.expand_env`` replaced three divergent implementations. These
tests pin the surviving semantics so a future refactor cannot silently widen
substitution or change how a committed provider registry expands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from auto_router import config, fleet_task_dispatcher, offline_guard
from auto_router.env_expansion import ENV_PATTERN, expand_env

REPO_ROOT = Path(__file__).resolve().parents[1]

# The two pre-consolidation patterns. ``config``/``offline_guard`` accepted any
# name without ':' or '}'; ``fleet_task_dispatcher`` accepted only shell
# identifiers. The shared helper keeps the stricter rule.
LOOSE_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")
STRICT_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

assert ENV_PATTERN.pattern == STRICT_PATTERN.pattern, "shared pattern must be the strict one"


def test_substitutes_a_set_variable() -> None:
    assert expand_env("http://${HOST}:1234/v1", {"HOST": "node-a"}) == "http://node-a:1234/v1"


def test_unset_variable_falls_back_to_default() -> None:
    assert expand_env("${MISSING:-fallback}", {}) == "fallback"


def test_unset_variable_without_default_expands_to_empty_string() -> None:
    assert expand_env("a${MISSING}b", {}) == "ab"


def test_set_variable_wins_over_default() -> None:
    assert expand_env("${HOST:-fallback}", {"HOST": "real"}) == "real"


def test_defaults_may_contain_slashes_colons_and_dots() -> None:
    token = "${LMSTUDIO_X1_BASE_URL:-http://host.docker.internal:1234/v1}"
    assert expand_env(token, {}) == "http://host.docker.internal:1234/v1"


def test_default_may_be_empty() -> None:
    assert expand_env("${RECONCILIATION_TAILSCALE_BASE_URL:-}", {}) == ""


def test_multiple_tokens_in_one_value() -> None:
    value = "${A:-a}-${B:-b}-${C:-c}"
    assert expand_env(value, {"B": "bee"}) == "a-bee-c"


def test_recurses_into_lists_and_dicts() -> None:
    payload = {
        "providers": [
            {"name": "one", "base_url": "${ONE:-http://one:1234/v1}"},
            {"name": "two", "base_url": "${TWO:-http://two:1234/v1}"},
        ],
        "meta": ["${TAG:-unset}", 7, True, None],
    }
    assert expand_env(payload, {"TWO": "http://override:1234/v1"}) == {
        "providers": [
            {"name": "one", "base_url": "http://one:1234/v1"},
            {"name": "two", "base_url": "http://override:1234/v1"},
        ],
        "meta": ["unset", 7, True, None],
    }


def test_non_string_scalars_pass_through_unchanged() -> None:
    assert expand_env(7, {}) == 7
    assert expand_env(None, {}) is None
    assert expand_env(True, {}) is True


def test_expansion_is_idempotent_for_already_expanded_values() -> None:
    once = expand_env("${HOST:-host}", {"HOST": "node-a"})
    assert expand_env(once, {"HOST": "node-a"}) == once


def test_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_ROUTER_TEST_EXPANSION", "from-os")
    assert expand_env("${AUTO_ROUTER_TEST_EXPANSION:-unset}") == "from-os"
    assert expand_env("${AUTO_ROUTER_TEST_ABSENT:-unset}") == "unset"


@pytest.mark.parametrize(
    "name",
    ["", "1BAD", "has-dash", "has.dot", "has space", "has:colon"],
)
def test_non_identifier_names_are_left_literal(name: str) -> None:
    """The unified pattern is stricter, so it can only narrow substitution."""

    token = "${" + name + ":-x}"
    assert expand_env(token, {}) == token


def test_all_committed_tokens_use_identifier_safe_names() -> None:
    """No committed configuration may depend on the looser pattern.

    Only real configuration files are audited. Test sources legitimately contain
    deliberately malformed probe strings, and Python sources contain no `${...}`
    templates at all.
    """

    offenders: list[str] = []
    checked = 0
    for root in ("config", "archive"):
        for path in (REPO_ROOT / root).rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in LOOSE_PATTERN.finditer(text):
                checked += 1
                name = match.group(1)
                if STRICT_PATTERN.fullmatch("${" + name + "}") is None:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: ${{{name}}}")
    assert checked >= 14, f"expected the committed ${...} tokens to be found, saw {checked}"
    assert offenders == [], f"tokens the strict pattern would not expand: {offenders}"


@pytest.mark.parametrize(
    ("pattern", "value", "env", "expected"),
    [
        (STRICT_PATTERN, "${HOST:-http://a:1234/v1}", {}, "http://a:1234/v1"),
        (STRICT_PATTERN, "${HOST}", {"HOST": "h"}, "h"),
        (STRICT_PATTERN, "${HOST:-}", {}, ""),
        (LOOSE_PATTERN, "${HOST:-http://a:1234/v1}", {}, "http://a:1234/v1"),
        (LOOSE_PATTERN, "${HOST}", {"HOST": "h"}, "h"),
        (LOOSE_PATTERN, "${HOST:-}", {}, ""),
    ],
)
def test_shared_helper_matches_both_original_patterns_on_identifier_names(
    pattern: re.Pattern[str],
    value: str,
    env: dict[str, str],
    expected: str,
) -> None:
    """For every identifier-shaped token the three patterns always agreed."""

    legacy = pattern.sub(lambda m: str(env.get(m.group(1), m.group(2) or "")), value)
    assert legacy == expected
    assert expand_env(value, env) == expected


def test_all_three_modules_delegate_to_the_shared_expander() -> None:
    assert config._expand_env is expand_env
    assert fleet_task_dispatcher._expand_env is expand_env
    # offline_guard calls the shared expander positionally with an env mapping.
    assert offline_guard.expand_env is expand_env


def test_no_module_redefines_the_env_pattern() -> None:
    for module in (config, offline_guard, fleet_task_dispatcher):
        assert not hasattr(module, "_ENV_PATTERN"), (
            f"{module.__name__} still defines a private _ENV_PATTERN"
        )


def test_dispatcher_string_only_call_shape_is_equivalent() -> None:
    """fleet_task_dispatcher expanded a bare string; verify that shape."""

    row = {"base_url": "${LMSTUDIO_X_BASE_URL:-http://host.docker.internal:1234/v1}"}
    result = fleet_task_dispatcher._expand_env(str(row.get("base_url") or "")).strip()
    assert result == "http://host.docker.internal:1234/v1"
