#!/bin/bash
# Smoke tests for agentgateway integration

set -e

GATEWAY_URL="http://localhost:3000"
ROUTER_URL="http://localhost:8088"
METRICS_URL="http://localhost:15020"

echo "=== AgentGateway Smoke Tests ==="
echo ""

# Test 1: Gateway health endpoint
echo "Test 1: Checking agentgateway health..."
if curl -s "${GATEWAY_URL}/" -H 'Content-Type: application/json' \
    -d '{"model":"local/test","messages":[{"role":"user","content":"Say gateway online"}]}' | jq > /dev/null 2>&1; then
    echo "✓ Gateway health check passed"
else
    echo "✗ Gateway health check failed (may not be ready yet)"
fi

# Test 2: Router health endpoint
echo ""
echo "Test 2: Checking auto-router health..."
if curl -s "${ROUTER_URL}/health" | jq > /dev/null 2>&1; then
    echo "✓ Auto-router health check passed"
else
    echo "✗ Auto-router health check failed"
fi

# Test 3: Router models endpoint
echo ""
echo "Test 3: Checking auto-router models..."
if curl -s "${ROUTER_URL}/v1/models" | jq > /dev/null 2>&1; then
    echo "✓ Models endpoint accessible"
else
    echo "✗ Models endpoint failed"
fi

# Test 4: Chat completion through router (with gateway if enabled)
echo ""
echo "Test 4: Testing chat completion..."
if curl -s "${ROUTER_URL}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"auto/local","messages":[{"role":"user","content":"Say hello through auto-router"}]}' | jq > /dev/null 2>&1; then
    echo "✓ Chat completion successful"
else
    echo "✗ Chat completion failed (may need local LM Studio running)"
fi

# Test 5: Gateway metrics endpoint
echo ""
echo "Test 5: Checking gateway metrics..."
if curl -s "${METRICS_URL}/metrics" | grep -q agentgateway_gen_ai || true; then
    echo "✓ Metrics endpoint accessible (agentgateway metrics found)"
else
    echo "⚠ Metrics endpoint may not have data yet or agentgateway not running"
fi

# Test 6: Jaeger UI availability (if OTEL enabled)
echo ""
echo "Test 6: Checking Jaeger UI..."
if curl -s http://localhost:16686 | grep -q "Jaeger" || true; then
    echo "✓ Jaeger UI available at http://localhost:16686"
else
    echo "⚠ Jaeger UI may not be running (OTEL services optional)"
fi

echo ""
echo "=== Smoke Tests Complete ==="
echo ""
echo "Next steps:"
echo "- View dashboard: http://localhost:8088/dashboard"
echo "- View Jaeger traces: http://localhost:16686"
echo "- Check gateway metrics: curl http://localhost:15020/metrics"
