from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


class LoadTestMode(str, Enum):
    COUNT = "COUNT"
    DURATION = "DURATION"


class LoadTestTrafficMode(str, Enum):
    REQUESTS = "REQUESTS"
    SCENARIO = "SCENARIO"


class LoadTestRunStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def empty_metrics(target_names: list[str] | None = None) -> dict[str, Any]:
    base = {
        "total_requests": 0, "successful_requests": 0, "failed_requests": 0,
        "requests_per_second": 0.0, "average_ms": 0.0, "min_ms": 0.0,
        "max_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
        "status_counts": {}, "error_counts": {},
    }
    base["target_metrics"] = {
        name: {**base, "target_metrics": {}} for name in (target_names or [])
    }
    base["scenario_metrics"] = {
        "total_scenarios": 0, "successful_scenarios": 0, "failed_scenarios": 0,
        "scenarios_per_second": 0.0, "average_ms": 0.0, "p50_ms": 0.0,
        "p95_ms": 0.0, "p99_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0,
    }
    return base


@dataclass(frozen=True, slots=True)
class LoadTestRecord:
    id: UUID
    project_id: UUID
    name: str
    description: str
    targets: list[dict[str, Any]]
    mode: LoadTestMode
    request_count: int
    duration_seconds: int
    concurrency: int
    interval_ms: int
    timeout_seconds: float
    state_version: int
    created_at: datetime
    updated_at: datetime
    environment_id: UUID | None = None
    traffic_mode: LoadTestTrafficMode = LoadTestTrafficMode.REQUESTS
    initial_variables: dict[str, Any] = field(default_factory=dict)
    stop_on_failure: bool = True

    def updated(self, **changes: Any) -> "LoadTestRecord":
        return replace(
            self, **changes, state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class LoadTestRunRecord:
    id: UUID
    load_test_id: UUID
    status: LoadTestRunStatus
    mode: LoadTestMode
    started_at: datetime
    finished_at: datetime | None
    metrics: dict[str, Any]
    error_message: str | None