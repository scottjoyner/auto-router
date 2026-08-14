# Agent infrastructure visibility contract

This document defines the minimum evidence an autonomous operator (Hermes, AssistX workers, deployment agents, or human-assisted agents) must collect before changing networking, ingress, Docker, Tailscale, Caddy, storage-adjacent mounts, or service discovery.

The purpose is to stop agents from inferring topology from stale files or partial observations. A deployment is only safe when the agent can describe the current state, the intended state, the delta, the blast radius, and the rollback.

## Required visibility before proposing a change

### Host identity and reachability

Collect:

```bash
hostname
hostname -f 2>/dev/null || true
ip -br addr
ip route
tailscale status
tailscale ip -4
tailscale ip -6 2>/dev/null || true
```

Record:

- host name and role
- LAN addresses
- tailnet addresses and MagicDNS/FQDN
- whether Tailscale is direct/relayed/offline
- whether another tailnet node on the same LAN can be used as a recovery jump host

### Host health gate

Before deployment work, collect:

```bash
systemctl is-system-running || true
systemctl --failed --no-pager || true
df -hT
findmnt
sudo dmesg -T | grep -Ei 'I/O error|buffer I/O|ext4|xfs|btrfs|nvme|ata|reset|corrupt|read-only' | tail -n 100
```

If there are active filesystem I/O errors, read-only remounts, storage-controller errors, or the Docker daemon is unavailable because of host/storage failure, stop deployment reconciliation. Do not restart unrelated services in an attempt to make the health checks green.

### Docker inventory

Collect:

```bash
docker version
docker info
docker compose ls
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
docker volume ls
```

For each service in scope, capture `docker inspect` and record:

- container name and immutable image ID/digest where available
- Compose project/service labels
- command/entrypoint
- restart policy
- health status and healthcheck definition
- networks, aliases, and container IPs
- host port publications
- mounts and named volumes
- environment variable **names only**, with all secret values redacted
- dependencies that must remain available during changes

The agent must distinguish "container exists" from "service is healthy" and "service is reachable end-to-end".

### Tailscale container state

For every Tailscale sidecar or Tailscale-enabled container, collect from inside that container:

```bash
tailscale version
tailscale status
tailscale ip -4
tailscale ip -6 2>/dev/null || true
tailscale serve status --json || true
```

Record:

- tailnet hostname/device name
- Tailscale image tag and version
- authentication method category (auth key, OAuth, workload identity), never the credential value
- advertised tags
- persistent `TS_STATE_DIR` mount
- `TS_AUTH_ONCE` setting
- userspace vs kernel networking
- accepted DNS/routes
- Serve/Funnel configuration
- health/metrics endpoint configuration

A sidecar with no persistent state is not production-ready.

### Caddy or other reverse proxy state

Before changing ingress, collect:

```bash
# Adjust container name as needed.
docker logs --tail 200 <caddy-container> 2>&1 || true
docker inspect <caddy-container>
```

Also capture the active Caddyfile/config and validate it with the running Caddy version where possible.

Record:

- which component owns ports 80/443
- which Docker networks Caddy joins
- upstream service DNS names and ports
- host-header/rewrite/path-strip behavior
- certificate source
- whether Caddy is serving LAN, tailnet, public internet, or more than one of these

Do not stop Caddy to test Tailscale Serve. Test new ingress in parallel or migrate one hostname/service at a time with a rollback.

### Application-specific reverse-proxy requirements

For Nextcloud, record at minimum:

- final external HTTPS hostname
- trusted domains
- trusted proxies
- overwrite host/protocol/webroot settings
- web server listen port
- database/cache dependencies
- WebDAV and `.well-known` behavior

For AssistX/auto-router/Sophia/API services, record:

- internal service DNS name and port
- health endpoint
- authentication boundary
- websocket/SSE/streaming requirements
- callback/event-sink URLs
- dependencies such as Redis/Neo4j/LM Studio

## End-to-end evidence

A service is only considered working after checks from three vantage points where applicable:

1. **Inside the application namespace**: loopback/application health succeeds.
2. **Inside the Docker dependency network**: peer service can reach the service by Docker DNS/port.
3. **From an independent tailnet node**: MagicDNS/FQDN resolves and the real HTTPS/API request succeeds.

For HTTP/HTTPS, record status, redirect chain, response headers relevant to proxying, and TLS hostname. For APIs, run a representative request, not just a TCP connect.

## Change-plan format required from an agent

Before writes, the agent must state:

- Current state: what is running and how traffic flows now.
- Desired state: exact hostname -> ingress -> container -> port path.
- Delta: files/services/settings to change.
- Preconditions: credentials, ACL/tags, DNS/HTTPS enablement, Docker network existence.
- Non-goals: components that will not be touched.
- Risk/blast radius: which services can be interrupted.
- Validation: exact checks that prove success.
- Rollback: exact commands/config revision needed to return to prior state.

If any of these cannot be determined from available evidence, the agent must ask for the missing visibility instead of guessing.

## Approval boundaries

The following operations must never be hidden inside a broad "fix networking" approval:

- stopping/removing/recreating an existing ingress container
- removing Docker networks or volumes
- pruning Docker objects
- changing `/etc/fstab` or mounting/unmounting filesystems
- changing firewall policy
- resetting/removing Tailscale state or identity
- enabling Funnel/public exposure
- changing DNS records or public ingress
- changing ACL/grant policy
- rotating/deleting credentials

Each destructive or externally visible change must be separately identifiable and reversible.

## Repository evidence to preserve

Whenever an agent completes infrastructure work, commit or update:

- Compose/stack manifests
- Caddy/Tailscale Serve configuration
- `.env.example` variable names (never secrets)
- deployment runbook
- validation commands
- rollback procedure
- host/service inventory assumptions
- known unresolved blockers

The final agent report must include the Git commit SHA(s), the services actually validated, and anything that was only documented but not deployed.
