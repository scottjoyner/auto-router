from types import SimpleNamespace

from auto_router.main import _normalize_response_payload


def test_normalize_response_payload_injects_auto_router_metadata() -> None:
    response = SimpleNamespace(provider="groq", model="llama", data={"id": "x", "choices": []}, usage={})
    payload = _normalize_response_payload(response, "final", "interactive_balanced")

    assert payload["model"] == "llama"
    assert payload["auto_router"]["provider"] == "groq"
    assert payload["auto_router"]["stage"] == "final"
    assert payload["usage"] == {}
