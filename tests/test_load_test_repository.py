from uuid import uuid4

import pytest

from services.api.load_test_domain import (
    LoadTestMode, LoadTestRunStatus, LoadTestTrafficMode,
)
from services.api.load_test_repository import (
    LoadTestActiveRunConflict, LoadTestNameConflict, LoadTestRepository,
    LoadTestVersionConflict,
)


TARGETS = [{
    "name": "API", "method": "post", "url": "https://example.com/items",
    "headers": {}, "query": {}, "body": {"ok": True}, "expected_statuses": [201],
}]


def create(repository: LoadTestRepository, project_id=None, name="Load"):
    return repository.create_load_test(
        project_id or uuid4(), name, "", TARGETS, LoadTestMode.COUNT, 10, 5, 2, 10, 3,
    )


def test_crud_normalization_versioning_and_name_conflict() -> None:
    repository, project_id, environment_id = LoadTestRepository(), uuid4(), uuid4()
    created = repository.create_load_test(
        project_id, "  Load  ", "", TARGETS, LoadTestMode.COUNT, 10, 5, 2, 10, 3,
        environment_id=environment_id, category="  Checkout  ",
    )
    assert created.name == "Load"
    assert created.environment_id == environment_id
    assert created.category == "Checkout"
    assert created.traffic_mode == LoadTestTrafficMode.REQUESTS
    assert created.initial_variables == {}
    assert created.stop_on_failure is True
    assert created.targets[0]["method"] == "POST"
    with pytest.raises(LoadTestNameConflict):
        create(repository, project_id)

    scenario_targets = [{**TARGETS[0], "extractors": {"item_id": "data.0.id"}}]
    updated = repository.update_load_test(
        created.id, name="Load", description="changed",
        targets=scenario_targets, mode=LoadTestMode.DURATION, request_count=10,
        duration_seconds=5, concurrency=2, interval_ms=10, timeout_seconds=3,
        expected_version=0, environment_id=None,
        traffic_mode=LoadTestTrafficMode.SCENARIO,
        initial_variables={"seed": {"value": 1}}, stop_on_failure=False,
        category="Regression",
    )
    assert updated.state_version == 1
    assert updated.environment_id is None
    assert updated.traffic_mode == LoadTestTrafficMode.SCENARIO
    assert updated.initial_variables == {"seed": {"value": 1}}
    assert updated.stop_on_failure is False
    assert updated.category == "Regression"
    with pytest.raises(LoadTestVersionConflict):
        repository.update_load_test(
            created.id, name="Load", description="stale",
            targets=TARGETS, mode=LoadTestMode.COUNT, request_count=1,
            duration_seconds=1, concurrency=1, interval_ms=0, timeout_seconds=1,
            expected_version=0,
        )


def test_extractors_require_scenario_traffic_mode() -> None:
    repository = LoadTestRepository()
    targets = [{**TARGETS[0], "extractors": {"item_id": "data.id"}}]
    with pytest.raises(ValueError, match="only supported in SCENARIO"):
        repository.create_load_test(
            uuid4(), "Invalid", "", targets, LoadTestMode.COUNT, 1, 1, 1, 0, 1,
        )


def test_only_one_active_run_and_terminal_run_releases_guard() -> None:
    repository = LoadTestRepository()
    definition = create(repository)
    run = repository.create_run(definition.id)
    with pytest.raises(LoadTestActiveRunConflict):
        repository.create_run(definition.id)
    repository.update_run(
        run.id, status=LoadTestRunStatus.SUCCEEDED, metrics=run.metrics,
        finished_at=run.started_at,
    )
    assert repository.create_run(definition.id).status == LoadTestRunStatus.RUNNING


def test_recovery_fails_orphaned_runs_and_terminal_state_cannot_regress() -> None:
    repository = LoadTestRepository()
    definition = create(repository)
    run = repository.create_run(definition.id)

    assert repository.recover_running_runs("service restarted") == 1
    recovered = repository.get_run(run.id)
    assert recovered.status == LoadTestRunStatus.FAILED
    assert recovered.error_message == "service restarted"
    assert recovered.finished_at is not None

    unchanged = repository.update_run(
        run.id, status=LoadTestRunStatus.RUNNING,
        metrics={**run.metrics, "total_requests": 99},
    )
    assert unchanged == recovered
    assert repository.create_run(definition.id).status == LoadTestRunStatus.RUNNING


def test_response_details_are_paginated_and_filtered() -> None:
    repository = LoadTestRepository()
    run = repository.create_run(create(repository).id)
    repository.add_run_responses(run.id, [{
        "sequence": index, "target": "API", "successful": index != 2,
        "status": 200 if index != 2 else 500, "latency_ms": float(index),
        "error": None if index != 2 else "unexpected_status",
        "body": f"body-{index}", "body_kind": "text", "truncated": False,
        "body_bytes": 6,
    } for index in range(1, 4)])

    page, total = repository.list_run_responses(run.id, offset=1, limit=1)
    failures, failure_total = repository.list_run_responses(
        run.id, offset=0, limit=20, successful=False,
    )
    assert total == 3 and page[0].sequence == 2
    assert failure_total == 1 and failures[0].body == "body-2"