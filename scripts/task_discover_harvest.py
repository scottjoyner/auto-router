#!/usr/bin/env python3
"""Discover AssistX backlog work from idle fleet nodes.

This script is intentionally read-only. It checks node load over SSH, queries the
AssistX backlog dry-run endpoint, checks Redis backlog length, and writes a local
JSON harvest summary. Configuration is environment-driven so the repo does not
encode one fixed deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


DEFAULT_FLEET = {
    "x1-370": ("scott", "x1-370"),
    "deathstar-XPS-8920": ("deathstar", "deathstar-XPS-8920"),
    "destroyer": ("scott", "destroyer"),
    "scotts-macbook-air": ("scottjoyner", "scotts-macbook-air"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FleetNode:
    name: str
    user: str
    host: str

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.host}"


def parse_fleet(value: str | None) -> list[FleetNode]:
    if not value:
        return [FleetNode(name, user, host) for name, (user, host) in DEFAULT_FLEET.items()]
    nodes: list[FleetNode] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        # Format: name=user@host, or user@host where name defaults to host.
        if "=" in item:
            name, target = item.split("=", 1)
        else:
            target = item
            name = item.rsplit("@", 1)[-1]
        if "@" not in target:
            raise ValueError(f"Invalid fleet target {item!r}; expected name=user@host or user@host")
        user, host = target.split("@", 1)
        nodes.append(FleetNode(name=name.strip() or host.strip(), user=user.strip(), host=host.strip()))
    return nodes


def run_command(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def check_agent_load(node: FleetNode, ssh_key: str | None, timeout: int = 10) -> float:
    ssh_args = ["ssh", "-o", f"ConnectTimeout={timeout}"]
    if ssh_key:
        ssh_args.extend(["-i", ssh_key])
    ssh_args.extend([node.ssh_target, "cat /proc/loadavg"])
    try:
        result = run_command(ssh_args, timeout=timeout + 2)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.split()[0])
        print(f"   Load check failed: {result.stderr.strip() or result.stdout.strip() or 'no output'}")
    except Exception as exc:
        print(f"   Load check exception: {exc}")
    return 999.0


def fetch_assistx_tasks(base_url: str, limit: int = 50, timeout: int = 30) -> list[dict[str, Any]]:
    query = urlencode({"limit": limit, "queue": "backlog", "dry_run": "true"})
    url = f"{base_url.rstrip('/')}/api/backlog/tasks?{query}"
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - operator-supplied internal URL
            payload = response.read().decode("utf-8")
        data = json.loads(payload) if payload.strip() else []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return [item for item in data["tasks"] if isinstance(item, dict)]
    except Exception as exc:
        print(f"   AssistX fetch failed: {exc}")
    return []


def check_redis_queue_size(redis_host: str, redis_port: int, timeout: int = 10) -> int:
    try:
        result = run_command(["redis-cli", "-h", redis_host, "-p", str(redis_port), "LLEN", "backlog"], timeout=timeout)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
        print(f"   Redis check failed: {result.stderr.strip() or result.stdout.strip() or 'no output'}")
    except Exception as exc:
        print(f"   Redis check exception: {exc}")
    return 0


def extract_work_insights(tasks: list[dict[str, Any]], queue_size: int, node: FleetNode, load: float) -> dict[str, Any]:
    priority_counts: dict[str, int] = {}
    task_types: set[str] = set()
    urgent_count = 0
    for task in tasks:
        priority = str(task.get("priority") or "background")
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        status = str(task.get("status") or "unknown").lower()
        if status not in {"completed", "done", "closed"}:
            urgent_count += 1
        task_types.add(str(task.get("type") or task.get("task_type") or "unknown"))
    return {
        "timestamp": utc_now(),
        "agent": node.name,
        "host": node.host,
        "load_at_harvest": load,
        "total_tasks": len(tasks),
        "redis_backlog_size": queue_size,
        "tasks_by_priority": priority_counts,
        "task_types": sorted(task_types),
        "urgent_count": urgent_count,
        "sample_tasks": [
            {
                "id": task.get("id") or task.get("task_id"),
                "title": task.get("title") or task.get("name"),
                "priority": task.get("priority"),
                "status": task.get("status"),
            }
            for task in tasks[:10]
        ],
    }


def save_report(insights: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_file = reports_dir / f"{insights['agent']}_harvest_{timestamp}.json"
    report_file.write_text(json.dumps(insights, indent=2, default=str), encoding="utf-8")
    return report_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover AssistX backlog work from idle fleet nodes")
    parser.add_argument("--assistx-url", default=os.getenv("ASSISTX_BASE_URL", "http://assistx:8000"))
    parser.add_argument("--redis-host", default=os.getenv("REDIS_HOST", "x1-370"))
    parser.add_argument("--redis-port", type=int, default=int(os.getenv("REDIS_PORT", "6379")))
    parser.add_argument("--ssh-key", default=os.getenv("HERMES_FLEET_SSH_KEY", str(Path.home() / ".ssh" / "hermes-agent-key")))
    parser.add_argument("--fleet", default=os.getenv("HERMES_FLEET_NODES"), help="Comma-separated name=user@host entries")
    parser.add_argument("--idle-load", type=float, default=float(os.getenv("HERMES_IDLE_LOAD", "1.0")))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--reports-dir", type=Path, default=Path(os.getenv("HERMES_HARVEST_DIR", str(Path.home() / ".hermes" / "knowledge-harvest"))))
    args = parser.parse_args()

    ssh_key = args.ssh_key if args.ssh_key and Path(args.ssh_key).exists() else None
    nodes = parse_fleet(args.fleet)
    print(f"Starting task discovery harvest at {utc_now()}")
    print(f"AssistX: {args.assistx_url}")
    print(f"Fleet nodes: {', '.join(node.name for node in nodes)}")

    all_insights: list[dict[str, Any]] = []
    for node in nodes:
        print(f"\nChecking {node.name} ({node.ssh_target})...")
        load = check_agent_load(node, ssh_key=ssh_key)
        print(f"   Load: {load:.2f}")
        if load >= args.idle_load:
            print(f"   Busy or unreachable (load >= {args.idle_load:.2f}); skipping")
            continue
        tasks = fetch_assistx_tasks(args.assistx_url, limit=args.limit)
        queue_size = check_redis_queue_size(args.redis_host, args.redis_port)
        insights = extract_work_insights(tasks, queue_size, node, load)
        all_insights.append(insights)
        report_file = save_report(insights, args.reports_dir)
        print(f"   Found {len(tasks)} backlog tasks; Redis backlog={queue_size}; report={report_file}")

    summary = {"timestamp": utc_now(), "agents_harvested": len(all_insights), "insights": all_insights}
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    summary_file = args.reports_dir / "harvest_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nHarvest complete; summary={summary_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
