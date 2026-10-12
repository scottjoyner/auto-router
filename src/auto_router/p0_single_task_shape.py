"""P0 #37 / #61: strict request-shape research gate for ONE native task.

The Prism completion route can allocate MANY native server_task.id values:
one per prompt input, plus n-1 child tasks. The current experimental
per-attempt native receipt covers only one task. Until trusted native
task-allocation evidence and complete parent/child receipt accounting
exist, fail closed on batch/parallel/ambiguous request shapes.

This pure function accepts ONLY the original request body from the
signed v2 ASGI middleware boundary. It does not install into any router
route, execute inference, prove that a model uses defaults, or permit
a physical canary. A valid shape is a *candidate*, NOT an admitted task.
"""
from __future__ import annotations

import hmac
import json
import re
import uuid
from typing import Any

from auto_router.user_delegation import delegated_body_digest

_ALLOWED_FIELDS = frozenset({"model", "messages", "max_tokens", "stream"})
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class SingleTaskShapeRejected(ValueError):
    """Fixed, non-sensitive error: never print the original prompt."""


def _uuid4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def qualify_strict_single_native_task_shape(
    *,
    verified_actor: dict[str, Any],
    original_request_body: dict[str, Any],
) -> dict[str, Any]:
    """Examine narrow synthetic physical-canary SHAPE, not native proof.

    The caller MUST obtain verified_actor from
    ASGI scope["auto_router.p0_verified_user_delegation"] and body from
    the ORIGINAL gateway-verified JSON before executor metadata injection.
    Calling this function with a dictionary copied from untrusted client
    JSON does not authenticate anything.
    """
    if not isinstance(verified_actor, dict) or not isinstance(
        original_request_body, dict
    ):
        raise SingleTaskShapeRejected("verified actor and original JSON required")
    if (
        verified_actor.get("issuer") != "trusted-user-gateway-p0"
        or not _uuid4(verified_actor.get("root_request_id"))
        or not _uuid4(verified_actor.get("attempt_id"))
        or verified_actor["root_request_id"] == verified_actor["attempt_id"]
        or verified_actor.get("request_path") != "/v1/chat/completions"
        or not isinstance(verified_actor.get("request_sha256"), str)
        or not _SHA.fullmatch(verified_actor["request_sha256"])
    ):
        raise SingleTaskShapeRejected("signed v2 actor required")
    if (
        set(original_request_body) not in (
            {"model", "messages", "max_tokens"},
            _ALLOWED_FIELDS,
        )
        or not isinstance(original_request_body.get("model"), str)
        or not _MODEL.fullmatch(original_request_body["model"])
        or original_request_body["model"] != verified_actor.get("model_handle")
    ):
        raise SingleTaskShapeRejected("single-task request field/model restriction")
    messages = original_request_body.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or set(messages[0]) != {"role", "content"}
        or messages[0]["role"] != "user"
        or not isinstance(messages[0]["content"], str)
        or not 1 <= len(messages[0]["content"]) <= 512
        or "\x00" in messages[0]["content"]
        or any(
            ord(c) < 0x20 and c not in "\r\n\t"
            for c in messages[0]["content"]
        )
        or type(original_request_body.get("max_tokens")) is not int
        or not 1 <= original_request_body["max_tokens"] <= 16
        or (
            "stream" in original_request_body
            and type(original_request_body["stream"]) is not bool
        )
    ):
        raise SingleTaskShapeRejected("single plain-text canary prompt required")
    try:
        canonical = json.dumps(
            original_request_body, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SingleTaskShapeRejected("invalid canonical canary input") from exc
    if len(canonical) > 2048:
        raise SingleTaskShapeRejected("single-task original JSON exceeds bound")
    digest = delegated_body_digest(
        "/v1/chat/completions", original_request_body
    )
    if not hmac.compare_digest(verified_actor["request_sha256"], digest):
        raise SingleTaskShapeRejected("original signed request digest mismatch")
    return {
        "schema": "p0-single-native-task-shape-v1",
        "shape": "single_plain_text_chat_candidate",
        "expected_native_task_count": 1,
        "actual_native_task_count_observed": None,
        "native_parent_child_lineage_proven": False,
        "native_execution_attested": False,
        "physical_inference_permitted": False,
        "operator_approved": False,
        "response_delivery": "unknown",
    }
