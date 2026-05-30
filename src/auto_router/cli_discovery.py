from __future__ import annotations

import asyncio
import shutil
import socket
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CliCandidate:
    name: str
    command: str
    cli_type: str
    version_args: list[str] = field(default_factory=lambda: ["--version"])
    credit_hint: str | None = None
    notes: str = ""


@dataclass
class CliDiscoveryResult:
    name: str
    command: str
    cli_type: str
    installed: bool
    runnable: bool
    path: str | None
    node_id: str
    checked_at: int
    version: str | None = None
    error: str | None = None
    credit_hint: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "type": self.cli_type,
            "installed": self.installed,
            "runnable": self.runnable,
            "path": self.path,
            "node_id": self.node_id,
            "checked_at": self.checked_at,
            "version": self.version,
            "error": self.error,
            "credit_hint": self.credit_hint,
            "notes": self.notes,
        }


DEFAULT_CLI_CANDIDATES: list[CliCandidate] = [
    CliCandidate(
        name="codex",
        command="codex",
        cli_type="codex",
        credit_hint="subscription_reset_dependent",
        notes="Premium repo-critical lane; enabled only when subscription/quota is available.",
    ),
    CliCandidate(
        name="gemini-cli",
        command="gemini",
        cli_type="gemini_cli",
        credit_hint="credits_remaining_expected",
        notes="Useful for large-context review, documentation, and batch analysis while credits remain.",
    ),
    CliCandidate(
        name="opencode",
        command="opencode",
        cli_type="opencode",
        credit_hint="credits_remaining_expected",
        notes="Useful local/default coding-agent lane with write/commit disabled by policy unless explicitly approved.",
    ),
]


async def discover_cli_tools(
    candidates: list[CliCandidate] | None = None,
    timeout_seconds: float = 3.0,
    node_id: str | None = None,
) -> list[CliDiscoveryResult]:
    selected = candidates or DEFAULT_CLI_CANDIDATES
    return await asyncio.gather(
        *(discover_cli_tool(candidate, timeout_seconds=timeout_seconds, node_id=node_id) for candidate in selected)
    )


async def discover_cli_tool(
    candidate: CliCandidate,
    timeout_seconds: float = 3.0,
    node_id: str | None = None,
) -> CliDiscoveryResult:
    checked_at = int(time.time())
    host = node_id or socket.gethostname()
    path = shutil.which(candidate.command)
    if not path:
        return CliDiscoveryResult(
            name=candidate.name,
            command=candidate.command,
            cli_type=candidate.cli_type,
            installed=False,
            runnable=False,
            path=None,
            node_id=host,
            checked_at=checked_at,
            credit_hint=candidate.credit_hint,
            notes=candidate.notes,
            error="command not found in PATH",
        )

    try:
        process = await asyncio.create_subprocess_exec(
            candidate.command,
            *candidate.version_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        text = (stdout or stderr).decode("utf-8", errors="replace").strip()
        return CliDiscoveryResult(
            name=candidate.name,
            command=candidate.command,
            cli_type=candidate.cli_type,
            installed=True,
            runnable=process.returncode == 0,
            path=path,
            node_id=host,
            checked_at=checked_at,
            version=text.splitlines()[0][:200] if text else None,
            error=None if process.returncode == 0 else f"version check exited {process.returncode}: {text[:300]}",
            credit_hint=candidate.credit_hint,
            notes=candidate.notes,
        )
    except Exception as exc:
        return CliDiscoveryResult(
            name=candidate.name,
            command=candidate.command,
            cli_type=candidate.cli_type,
            installed=True,
            runnable=False,
            path=path,
            node_id=host,
            checked_at=checked_at,
            credit_hint=candidate.credit_hint,
            notes=candidate.notes,
            error=str(exc)[:500],
        )
