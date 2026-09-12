from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from .data_factory_domain import WorkflowDefinition
from .data_factory_executor import WorkflowRunRecord
from .data_factory_schemas import WorkflowDefinitionSchema


class WorkflowNotFound(LookupError):
    pass


class WorkflowConflict(ValueError):
    pass


class WorkflowNameConflict(WorkflowConflict):
    pass


class WorkflowVersionConflict(WorkflowConflict):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowRecord:
    id: UUID
    project_id: UUID
    name: str
    description: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    variables: dict[str, Any]
    state_version: int
    created_at: datetime
    updated_at: datetime

    def definition(self) -> WorkflowDefinition:
        return WorkflowDefinitionSchema.model_validate({
            "nodes": self.nodes, "edges": self.edges, "variables": self.variables,
        }).to_domain(str(self.id))


def normalize_workflow_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("workflow name must contain between 1 and 255 characters")
    return normalized


def normalize_definition(
    nodes: Sequence[Any], edges: Sequence[Any], variables: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    definition = WorkflowDefinitionSchema.model_validate({
        "nodes": nodes, "edges": edges, "variables": variables,
    }).model_dump(mode="json")
    return definition["nodes"], definition["edges"], definition["variables"]


class DataFactoryRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._workflows: dict[UUID, WorkflowRecord] = {}
        self._names: dict[tuple[UUID, str], UUID] = {}
        self._runs: dict[UUID, list[WorkflowRunRecord]] = {}

    def create_workflow(
        self, project_id: UUID, name: str, description: str,
        nodes: Sequence[Any], edges: Sequence[Any], variables: Mapping[str, Any],
    ) -> WorkflowRecord:
        normalized_name = normalize_workflow_name(name)
        normalized = normalize_definition(nodes, edges, variables)
        with self._lock:
            key = (project_id, normalized_name)
            if key in self._names:
                raise WorkflowNameConflict("a workflow with this project and name exists")
            now = datetime.now(timezone.utc)
            record = WorkflowRecord(
                uuid4(), project_id, normalized_name, description, *normalized, 0, now, now,
            )
            self._workflows[record.id] = record
            self._names[key] = record.id
            self._runs[record.id] = []
            return deepcopy(record)

    def get_workflow(self, workflow_id: UUID) -> WorkflowRecord:
        with self._lock:
            try:
                return deepcopy(self._workflows[workflow_id])
            except KeyError:
                raise WorkflowNotFound(f"workflow {workflow_id} was not found") from None

    def list_workflows(self, project_id: UUID) -> list[WorkflowRecord]:
        with self._lock:
            records = [item for item in self._workflows.values()
                       if item.project_id == project_id]
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def update_workflow(
        self, workflow_id: UUID, *, project_id: UUID, name: str, description: str,
        nodes: Sequence[Any], edges: Sequence[Any], variables: Mapping[str, Any],
        expected_version: int,
    ) -> WorkflowRecord:
        normalized_name = normalize_workflow_name(name)
        normalized = normalize_definition(nodes, edges, variables)
        with self._lock:
            current = self.get_workflow(workflow_id)
            if current.project_id != project_id:
                raise WorkflowConflict("a workflow cannot be moved between projects")
            if current.state_version != expected_version:
                raise WorkflowVersionConflict(
                    f"state version mismatch: expected {expected_version}, "
                    f"current {current.state_version}"
                )
            key = (project_id, normalized_name)
            owner = self._names.get(key)
            if owner is not None and owner != workflow_id:
                raise WorkflowNameConflict("a workflow with this project and name exists")
            updated = WorkflowRecord(
                current.id, project_id, normalized_name, description, *normalized,
                current.state_version + 1, current.created_at, datetime.now(timezone.utc),
            )
            self._workflows[workflow_id] = updated
            if normalized_name != current.name:
                self._names.pop((project_id, current.name), None)
                self._names[key] = workflow_id
            return deepcopy(updated)

    def delete_workflow(self, workflow_id: UUID) -> None:
        with self._lock:
            current = self.get_workflow(workflow_id)
            del self._workflows[workflow_id]
            self._names.pop((current.project_id, current.name), None)
            self._runs.pop(workflow_id, None)

    def save_run(self, workflow_id: UUID, run: WorkflowRunRecord) -> WorkflowRunRecord:
        with self._lock:
            self.get_workflow(workflow_id)
            if run.workflow_id != str(workflow_id):
                raise WorkflowConflict("run workflow id does not match workflow")
            self._runs[workflow_id].append(deepcopy(run))
            return deepcopy(run)

    def list_runs(self, workflow_id: UUID) -> list[WorkflowRunRecord]:
        with self._lock:
            self.get_workflow(workflow_id)
            return deepcopy(sorted(
                self._runs[workflow_id], key=lambda item: item.started_at, reverse=True,
            ))


WorkflowRepository = DataFactoryRepository