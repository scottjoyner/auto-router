# Tailscale + Docker service networking

This document defines the supported pattern for exposing `auto-router`, AssistX, Nextcloud, Sophia, Hermes-adjacent services, and other Docker workloads to the tailnet without publishing every application port to the LAN or relying on ad-hoc host-level `tailscale serve` commands.

## Design goal

Application containers stay on private Docker networks. A Tailscale sidecar joins the tailnet with a stable identity and shares a network namespace with the application it exposes. HTTPS/Serve configuration belongs to the service deployment, is version controlled, and survives restarts.

Do **not** replace an existing reverse proxy or stop unrelated containers as part of tailnet enrollment. Host-level Tailscale, Caddy, and per-service Tailscale sidecars are separate concerns and must be changed independently.

## Recommended service pattern

For a service such as `auto-router`, run a Tailscale sidecar and put the application in the sidecar's network namespace with `network_mode: service:<tailscale-service>`.

```yaml
services:
  auto-router-ts:
    image: tailscale/tailscale:stable
    hostname: auto-router
    environment:
      TS_AUTHKEY: ${TAILSCALE_AUTHKEY}
      TS_STATE_DIR: /var/lib/tailscale
      TS_AUTH_ONCE: "true"
      TS_ACCEPT_DNS: "true"
      TS_SERVE_CONFIG: /config/serve.json
      TS_ENABLE_HEALTH_CHECK: "true"
      TS_LOCAL_ADDR_PORT: "[::]:9002"
    volumes:
      - auto-router-ts-state:/var/lib/tailscale
      - ./tailscale/auto-router:/config:ro
    restart: unless-stopped

  llm-router:
    build: .
    network_mode: service:auto-router-ts
    environment:
      AUTO_ROUTER_HOST: 0.0.0.0
      AUTO_ROUTER_PORT: 8088
    depends_on:
      auto-router-ts:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=3).read()"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

volumes:
  auto-router-ts-state:
```

The Tailscale container's persistent state directory is mandatory for stable identity. `TS_AUTH_ONCE=true` prevents needless re-authentication after the node is already registered. `TS_HOSTNAME`/`hostname` should be unique and intentional. Enable MagicDNS acceptance only where the service needs tailnet DNS.

Tailscale's container image uses userspace networking by default. That is the preferred first deployment because it requires fewer privileges. If a workload demonstrably needs kernel networking, set `TS_USERSPACE=false` and explicitly add `/dev/net/tun` plus the required capabilities; do not grant those privileges by default.

## Serve configuration

Use `TS_SERVE_CONFIG` for declarative HTTPS/Serve behavior. Mount the configuration as a **directory**, not as an individual file, so updates are detected correctly.

Example `tailscale/auto-router/serve.json`:

```json
{
  "TCP": {
    "443": {
      "HTTPS": true
    }
  },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": {
        "/": {
          "Proxy": "http://127.0.0.1:8088"
        }
      }
    }
  }
}
```

Before production use, generate or export a known-good Serve document using `tailscale serve status --json` on a test node and commit the resulting shape. Do not synthesize production Serve JSON from memory when the running Tailscale version can validate/export it.

## Nextcloud

Nextcloud should receive its own stable tailnet hostname instead of being hidden behind a path prefix such as `/nextcloud`. The preferred topology is:

```text
nextcloud.tailnet-name.ts.net:443 -> tailscale sidecar -> nextcloud:80
```

Keep the Nextcloud app and database on a private Docker network. Configure Nextcloud's trusted domains/proxies and overwrite protocol/host values for the final HTTPS hostname. Validate WebDAV, redirects, generated asset URLs, login, and `.well-known` endpoints before calling the deployment healthy.

Do not use path stripping as the default Nextcloud deployment strategy. A dedicated hostname avoids a large class of redirect and generated-URL problems.

## Caddy and Tailscale responsibilities

Use one of these two patterns for a given endpoint, not an accidental mixture:

1. **Per-service Tailscale ingress**: Tailscale sidecar owns the tailnet hostname and HTTPS Serve; the app listens only inside the shared network namespace.
2. **Central Caddy ingress**: Caddy owns HTTP routing and Tailscale provides host connectivity. Caddy and each backend share a Docker network. The backend remains unpublished to the LAN.

If Caddy is already deployed, test it before changing it. Never stop Caddy merely to test Tailscale Serve. A migration from Caddy to per-service Serve must be explicit, reversible, and performed one service at a time.

## Secrets and identity

Never commit an auth key. Supply `TS_AUTHKEY`, OAuth credentials, or workload identity through the deployment secret mechanism. Prefer tagged, reusable automation credentials for long-lived infrastructure and scope ACL/grant policy to the service tag.

Each sidecar must have a persistent `TS_STATE_DIR` volume. Without persistent state, container restarts create new tailnet nodes and break stable DNS/ACL expectations.

## Health and observability

For Tailscale 1.78+, set:

```text
TS_ENABLE_HEALTH_CHECK=true
TS_ENABLE_METRICS=true
TS_LOCAL_ADDR_PORT=[::]:9002
```

The orchestration health check should verify both planes independently:

- Tailscale sidecar owns at least one tailnet IP and `/healthz` returns 200.
- Application health endpoint succeeds via `127.0.0.1` in the shared namespace.
- A separate end-to-end probe from another tailnet node resolves the service MagicDNS/FQDN and reaches HTTPS.

A green Tailscale container does not prove the application works, and a green application container does not prove tailnet ingress works.

## Required validation for every service

Before declaring a service deployed, record:

- Docker host/node name and LAN/Tailscale identity.
- Compose project and service names.
- Docker networks and aliases.
- Application listen address/port inside the container.
- Whether any host ports are published and why.
- Tailscale sidecar hostname, state volume, image version, auth method, tags, and userspace/kernel mode.
- Current `tailscale status`, `tailscale ip`, and `tailscale serve status --json` output from the sidecar.
- DNS/MagicDNS resolution from at least one independent tailnet node.
- HTTP status, redirects, and TLS certificate hostname from an independent tailnet node.
- Application-specific proxy settings such as Nextcloud trusted domains/proxies.

## Agent change-safety contract

An autonomous agent configuring ingress must first collect evidence and produce a proposed delta. It must not combine unrelated lifecycle operations in one approval request.

Before any write, capture:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
docker compose ls
tailscale status
tailscale serve status --json || true
ss -lntup
```

For each relevant container also capture `docker inspect` networking, mounts, health, restart policy, and environment variable **names**. Secret values must be redacted.

A change that stops/removes a container, removes a network/volume, changes host firewall rules, changes `/etc/fstab`, resets Tailscale identity, or replaces the active ingress is destructive and requires an isolated approval with an explicit rollback.

## Recovery constraint

If the host reports storage I/O errors, filesystem corruption, read-only remounts, Docker daemon failure related to storage, or an unhealthy boot, freeze autonomous deployment changes. Use another reachable tailnet node on the same LAN as a recovery jump host where possible. Infrastructure reconciliation resumes only after the host is stable.
