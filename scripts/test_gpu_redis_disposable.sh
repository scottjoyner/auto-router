#!/usr/bin/env bash
# Disposable Redis test: no production Redis/credentials/GPU dispatch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_PORT=16389
TEST_NAME=gpu-fence-redis-disposable-20261016
if ! command -v docker >/dev/null; then
    echo 'Docker required for isolated Redis integration test' >&2
    exit 2
fi
if docker ps -a --format '{{.Names}}' | grep -Fqx "$TEST_NAME"; then
    echo 'Disposable Redis container name already exists' >&2
    exit 3
fi
if ss -lnt | grep -Eq "127[.]0[.]0[.]1:$TEST_PORT[[:space:]]"; then
    echo 'Disposable Redis port already occupied' >&2
    exit 4
fi
cleanup() {
    docker rm -f "$TEST_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run --rm -d --name "$TEST_NAME" --network host \
    --memory 128m --cpus 0.5 --pids-limit 32 \
    redis:7-alpine redis-server --bind 127.0.0.1 --port "$TEST_PORT" \
    --save "" --appendonly no --protected-mode yes >/dev/null
export GPU_TEST_REDIS_URL=redis://127.0.0.1:16389/0
PYTHON_BIN=$(command -v python3)
for i in 1 2 3 4 5; do
    if "$PYTHON_BIN" -c 'import redis; redis.Redis(host="127.0.0.1", port=16389, socket_connect_timeout=1).ping()'; then
        break
    fi
    sleep 1
done
(
    cd "$ROOT"
    "$PYTHON_BIN" -m pytest -q tests/test_gpu_distributed_lease.py -rs
)
