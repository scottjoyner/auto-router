"""Strict provider-returned token accounting; quota estimates never count as usage.

Counts are only observed when the upstream provider supplied usage metadata.
Missing counts remain unknown: persisted zero is NOT evidence of zero inference.
"""
from __future__ import annotations

from typing import Any


def provider_reported_usage(value: Any) -> tuple[int, int, int, bool]:
    if not isinstance(value, dict):
        return 0, 0, 0, False
    prompt_keys = ("prompt_tokens", "input_tokens")
    output_keys = ("completion_tokens", "output_tokens")
    allowed = (*prompt_keys, *output_keys, "total_tokens")
    provided = [key for key in allowed if key in value and value[key] is not None]
    if not provided:
        return 0, 0, 0, False
    if any(type(value[key]) is not int or value[key] < 0 for key in provided):
        return 0, 0, 0, False
    if (len({value[key] for key in prompt_keys if key in provided}) > 1
            or len({value[key] for key in output_keys if key in provided}) > 1):
        return 0, 0, 0, False
    prompt = next((value[key] for key in prompt_keys if key in provided), 0)
    output = next((value[key] for key in output_keys if key in provided), 0)
    total = value.get("total_tokens")
    if total is None:
        total = prompt + output
    elif total < prompt + output:
        return 0, 0, 0, False
    return prompt, output, total, True
