"""Common adapter contracts and execution helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from packages.protocol import ResultOutcome, TaskEnvelope


@dataclass(frozen=True)
class AdapterResult:
    """Structured, engine-independent result returned by every adapter."""

    outcome: ResultOutcome
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False


class Adapter(Protocol):
    def run(self, task: TaskEnvelope, workspace: Path) -> AdapterResult:
        """Execute a task inside workspace."""


def resolve_entrypoint(workspace: Path, entrypoint: str) -> Path:
    root = workspace.resolve()
    resolved = (root / entrypoint).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("entrypoint escapes workspace")
    if not resolved.is_file():
        raise ValueError("entrypoint must be an existing file")
    return resolved


def execution_timeout(task: TaskEnvelope, default: float = 300.0) -> float:
    remaining = deadline_remaining(task)
    requested = float(task.parameters.get("timeout", default))
    if requested <= 0:
        raise ValueError("timeout must be positive")
    return min(requested, remaining)


def deadline_remaining(task: TaskEnvelope) -> float:
    remaining = (task.deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("task deadline has passed")
    return remaining


def truncate(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    if len(text) <= limit:
        return text
    marker = "...[truncated]"
    if limit <= len(marker):
        return marker[:limit]
    return f"{text[:limit - len(marker)]}{marker}"