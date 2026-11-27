"""Hermetic regression: external graph projection must not turn string booleans
into True. `running: "false"` in a YAML/JSON projection previously made a node
look online because Python truthiness treats any non-empty string as True.

No network, DB, service, or fixture files are used; only the pure projection
helper is exercised.
"""
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from auto_router.config import _graph_node_context  # noqa: E402


def _running(raw, *, present=True):
    props = {"running": raw} if present else {}
    node = _graph_node_context({
        "kind": "node",
        "node_id": "proj-bool-node",
        "properties": props,
    })
    assert node is not None
    return node.running


def test_real_json_false_stays_false():
    assert _running(False) is False


def test_string_false_is_not_running():
    assert _running("false") is False


def test_capitalised_string_false_is_not_running():
    assert _running("False") is False


def test_upper_string_false_is_not_running():
    assert _running("FALSE") is False


def test_numeric_string_zero_is_not_running():
    assert _running("0") is False


def test_unknown_string_fails_closed():
    assert _running("yes") is False


def test_real_json_true_stays_true():
    assert _running(True) is True


def test_canonical_string_true_is_running():
    assert _running(" true ") is True


def test_absent_key_keeps_local_default():
    # A node with no `running` field keeps the existing default (local => True).
    assert _running(None, present=False) is True
