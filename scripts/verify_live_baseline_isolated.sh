#!/usr/bin/env bash
# Reproduce isolated, no-network ASGI acceptance against the locally verified runtime.
# No image pull/build, production mounts, provider requests, or production restart.
set -euo pipefail
head=${1:?supply exact candidate commit SHA}
[[ "$head" =~ ^[0-9a-f]{40}$ ]] || exit 2
repo=$(git rev-parse --show-toplevel)
image=auto-router:review-685346c-20261124
expected=sha256:ce1b4d6c746af5a8997a89d1810bc23bc8ff04d115cc7ff98414e8f7c3af7b42
[[ "$(docker image inspect --format '{{.Id}}' "$image")" == "$expected" ]]
d=$(mktemp -d /tmp/router-container-acceptance.XXXXXXXX)
trap 'rm -rf -- "$d"' EXIT
git -C "$repo" archive "$head" src config | tar -xf - -C "$d"
chmod 755 "$d"
timeout 18 docker run -i --rm --pull never --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges --memory 384m --memory-swap 384m \
  --cpus 0.5 --pids-limit 64 --user 65534:65534 \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --mount "type=bind,src=$d,dst=/candidate,readonly" \
  -e PYTHONPATH=/candidate/src -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp \
  -e AUTO_ROUTER_DATABASE_URL=sqlite:////tmp/fixture.sqlite3 -e AUTO_ROUTER_ADMIN_TOKEN= \
  -w /candidate --entrypoint python "$image" - <<'PY'
import asyncio
import inspect
import httpx
from auto_router.secure_live import app
from auto_router import main
from auto_router.owned_stream_response import OwnedStreamingResponse

assert inspect.getfile(main).startswith("/candidate/")
paths = set(app.openapi()["paths"])
required = {"/health", "/v1/models", "/v1/chat/completions",
            "/admin/admission", "/admin/runtime-projection"}
forbidden = {"/admin/backlog/burn-down", "/admin/live-models",
             "/admin/services/scan", "/jobs/agent"}
assert required <= paths, required - paths
assert not paths & forbidden, paths & forbidden
print("STRICT_ROUTE_SURFACE_PASS", len(paths))

async def test():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        for path in ("/admin/admission", "/admin/runtime-projection"):
            response = await client.get(path)
            assert response.status_code == 401, (path, response.status_code)
        response = await client.post(
            "/v1/chat/completions", json={"model": "fixture", "messages": []}
        )
        assert response.status_code == 401, response.status_code
    print("AUTH_DENIAL_3_PASS")

asyncio.run(test())
PY
# This is import/route/auth-denial acceptance only. No lifespan or real provider acceptance.
