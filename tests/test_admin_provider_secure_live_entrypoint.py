"""Exact production ASGI entrypoint: provider data is redacted behind admin auth.

An isolated subprocess creates no application lifespan or real provider
connection. It uses only synthetic credentials and in-memory SQLite.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_secure_live_mounted_provider_inventory_redaction():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "AUTO_ROUTER_STRICT_OFFLINE": "true",
        "AUTO_ROUTER_DATABASE_URL": "sqlite:///:memory:",
        "AUTO_ROUTER_DATABASE_PLACEMENT_REQUIRED": "false",
        "AUTO_ROUTER_DATABASE_PLACEMENT_TEST_CONTEXT": "true",
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Source is constant, not a model-generated command, and never executes a
    # lifespan. Only the checked-in synthetic application is inspected.
    script = """
from types import SimpleNamespace
from fastapi.testclient import TestClient
from auto_router import main
from auto_router.secure_live import app, assert_mounted_admin_guards
import auto_router.security as sec

assert_mounted_admin_guards(app)
# No inherited agent-job command or artifact endpoints may survive even if
# they are GET-only. Legacy agent-job API is entirely retired in production.
from fastapi.routing import iter_route_contexts
for context in iter_route_contexts(app.routes):
    candidate = getattr(context, 'path', '') or ''
    assert not candidate.startswith('/jobs/agent'), candidate
token = 'synthetic-router-admin-credential'
sec.get_settings = lambda: SimpleNamespace(admin_token=token)
raw = {
    'name': 'fixture',
    'type': 'openai-compatible',
    'models': [{
        'alias': 'synthetic-model',
        'provider_model': 'synthetic/id',
        'future_secret': 'PRIVATE_NESTED',
    }],
    'base_url': 'https://user:password@example.invalid/?key=PRIVATE_QUERY',
    'access_urls': ['https://example.invalid/?token=PRIVATE_ACCESS'],
    'headers': {'Authorization': 'PRIVATE_BEARER'},
    'api_key_file': '/private/PRIVATE_FILE',
    'api_key_env': 'PRIVATE_ENV',
    'future_config': {'secret': 'PRIVATE_FUTURE'},
}
main.state.providers = SimpleNamespace(
    providers=[SimpleNamespace(model_dump=lambda: raw)]
)
client = TestClient(app, raise_server_exceptions=False)
assert client.get('/admin/providers').status_code == 401
assert client.get('/admin/providers', headers={'X-Admin-Token': 'wrong'}).status_code == 401
valid = client.get('/admin/providers', headers={'X-Admin-Token': token})
assert valid.status_code == 200, valid.status_code
response = valid.json()
assert response == {
    'providers': [{
        'name': 'fixture',
        'type': 'openai-compatible',
        'models': [{'alias': 'synthetic-model',
                    'provider_model': 'synthetic/id'}],
    }]
}, response
assert 'PRIVATE_' not in valid.text
assert raw['headers']['Authorization'] == 'PRIVATE_BEARER'
print('PRODUCTION_MOUNT_REDACTION_SYNTHETIC_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env, text=True, capture_output=True, timeout=25, check=False,
    )
    assert result.returncode == 0, result.stderr[-1200:]
    assert "PRODUCTION_MOUNT_REDACTION_SYNTHETIC_PASS" in result.stdout
