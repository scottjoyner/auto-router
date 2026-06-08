#!/usr/bin/env python3
"""Query AssistX backlog tasks via the dry-run endpoint."""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen


def fetch_tasks(base_url: str, limit: int, queue: str, timeout: int) -> object:
    query = urlencode({"limit": limit, "queue": queue, "dry_run": "true"})
    url = f"{base_url.rstrip('/')}/api/backlog/tasks?{query}"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - operator-supplied internal URL
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload.strip() else []


def task_rows(data: object) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        return [item for item in data["tasks"] if isinstance(item, dict)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Query AssistX backlog dry-run tasks")
    parser.add_argument("--assistx-url", default=os.getenv("ASSISTX_BASE_URL", "http://assistx:8000"))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--queue", default="backlog")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    args = parser.parse_args()

    try:
        data = fetch_tasks(args.assistx_url, args.limit, args.queue, args.timeout)
    except Exception as exc:
        print(f"AssistX request failed: {exc}")
        return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    tasks = task_rows(data)
    print(f"AssistX: {args.assistx_url}")
    print(f"Found {len(tasks)} task(s)")
    for task in tasks[:10]:
        title = task.get("title") or task.get("name") or "No title"
        priority = task.get("priority", "unknown")
        status = task.get("status", "unknown")
        task_id = task.get("id") or task.get("task_id") or "unknown"
        print(f"- [{priority}] {title} ({task_id}; status={status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
