# Fleet Task Dispatcher

Stable execution consumer for the LM Studio fleet. It dispatches small tasks (<4096 tokens) to idle LM Studio nodes across the Tailscale fleet and writes responses as markdown docs into the knowledge vault workspace, while leaving assignment governance to auto-assign and canonical task state to AssistX / Neo4j.

auto-assign owns claim/release/lease semantics. AssistX / Neo4j owns canonical task state.

## Hardened Features

- **EWMA latency/quality tracking** per node/model pair
- **Node health scoring** (fast nodes get more work)
- **Response quality validation** (rejects empty/short responses)
- **Power profiles** per device type (mobile vs desktop vs server)
- **Retry logic** with exponential backoff for transient failures
- **Metrics aggregation** and reporting
- **Systemd service** for 24/7 operation

## Usage

### Run once to all online nodes

```bash
cd /home/scott/git/auto-router
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher
```

### Run in loop mode (one task at a time)

```bash
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher --loop
```

### Run benchmarks

```bash
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher --bench
```

### Show fleet status

```bash
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher --status
```

### Show aggregated metrics

```bash
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher --metrics
```

### Show cost-per-token report

```bash
PYTHONPATH=src python3 -m auto_router.fleet_task_dispatcher --report
```

## Systemd Service (24/7 Operation)

Install the service:

```bash
sudo cp src/auto_router/fleet_task_dispatcher_service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fleet-task-dispatcher
sudo systemctl start fleet-task-dispatcher
```

Check status:

```bash
sudo systemctl status fleet-task-dispatcher
journalctl -u fleet-task-dispatcher -f
```

## Configuration

Environment variables:

- `LM_FLEET_VAULT_WORKSPACE` - Where responses are written (default: `/home/scott/knowledge/vault-workspace`)
- `LM_FLEET_TASK_POWER_WATTS` - Default power estimate for cost calculations (default: 65W)

## Node Power Profiles

Different devices have different power consumption during inference:

| Node | Power (W) | Notes |
|------|-----------|-------|
| deathstar-xps-8920 | 150 | RX480 GPU + CPU under load |
| destroyer | 120 | General Linux worker |
| iphone-12-pro-max | 5 | Mobile device |
| scott-lenovo-ideapad-330s-15ikb | 25 | Small laptop |
| scott-optiplex-9030-aio | 65 | AIO desktop |
| beelink-ryzen-7-mini-pc | 45 | Beelink Ryzen 7 Mini PC |
| scotts-macbook-air | 15 | MacBook Air (M-series) |
| x1-370 | 200 | Heavy workstation |
| xwing | 80 | Dev worker |

## Metrics

The dispatcher tracks per-node/model metrics:

- **Latency EWMA** - Exponentially weighted moving average of response time
- **Quality EWMA** - Score based on response length and token efficiency
- **Success rate** - Ratio of successful runs to total attempts
- **Average cost per million tokens** - Power consumption normalized per million tokens

## Response Quality

Responses are validated before being written to the vault:

- Minimum 20 characters required
- Quality score = min(1.0, len/500) × min(1.0, output_tokens/input_tokens × 2)
- Empty or short responses trigger model fallback/retry

## Output Format

Responses are written as markdown files:

```markdown
# Task: <task prompt>

## Node: <node name>
## Model: <model key>

<response content>
```

Filenames follow the pattern: `<node_name>_<timestamp>.md`
