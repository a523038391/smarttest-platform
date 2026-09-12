from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .data_factory_executor import WorkflowRunRecord
from .data_factory_models import DataFactoryRunModel, DataFactoryWorkflowModel
from .data_factory_repository import (
    WorkflowConflict, WorkflowNameConflict, WorkflowNotFound, WorkflowRecord,
    WorkflowVersionConflict, normalize_definition, normalize_workflow_name,
)


class SqlDataFactoryRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_workflow(
        self, project_id: UUID, name: str, description: str,
        nodes: Sequence[Any], edges: Sequence[Any], variables: Mapping[str, Any],
    ) -> WorkflowRecord:
        normalized_name = normalize_workflow_name(name)
        normalized = normalize_definition(nodes, edges, variables)
        workflow_id = uuid4()
        now = datetime.now(timezone.utc)
        record = WorkflowRecord(
            workflow_id, project_id, normalized_name, description, *normalized, 0, now, now,
        )
        try:
            with self._session_factory() as session, session.begin():
                session.add(DataFactoryWorkflowModel(
                    id=str(workflow_id), project_id=str(project_id), name=normalized_name,
                    description=description, nodes=deepcopy(normalized[0]),
                    edges=deepcopy(normalized[1]), variables=deepcopy(normalized[2]),
                    state_version=0, created_at=now, updated_at=now,
                ))
        except IntegrityError:
            raise WorkflowNameConflict(
                "a workflow with this project and name exists"
            ) from None
        return record

    def get_workflow(self, workflow_id: UUID) -> WorkflowRecord:
        with self._session_factory() as session:
            model = session.get(DataFactoryWorkflowModel, str(workflow_id))
            if model is None:
                raise self._not_found(workflow_id)
            return self._workflow_record(model)

    def list_workflows(self, project_id: UUID) -> list[WorkflowRecord]:
        with self._session_factory() as session:
            query = select(DataFactoryWorkflowModel).where(
                DataFactoryWorkflowModel.project_id == str(project_id)
            ).order_by(DataFactoryWorkflowModel.created_at)
            return [self._workflow_record(item) for item in session.scalars(query)]

    def update_workflow(
        self, workflow_id: UUID, *, project_id: UUID, name: str, description: str,
        nodes: Sequence[Any], edges: Sequence[Any], variables: Mapping[str, Any],
        expected_version: int,
    ) -> WorkflowRecord:
        normalized_name = normalize_workflow_name(name)
        normalized = normalize_definition(nodes, edges, variables)
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(DataFactoryWorkflowModel).where(
                    DataFactoryWorkflowModel.id == str(workflow_id)
                ).with_for_update())
                if model is None:
                    raise self._not_found(workflow_id)
                current = self._workflow_record(model)
                if current.project_id != project_id:
                    raise WorkflowConflict("a workflow cannot be moved between projects")
                if current.state_version != expected_version:
                    raise WorkflowVersionConflict(
                        f"state version mismatch: expected {expected_version}, "
                        f"current {current.state_version}"
                    )
                now = datetime.now(timezone.utc)
                model.name = normalized_name
                model.description = description
                model.nodes = deepcopy(normalized[0])
                model.edges = deepcopy(normalized[1])
                model.variables = deepcopy(normalized[2])
                model.state_version += 1
                model.updated_at = now
                session.flush()
                return self._workflow_record(model)
        except IntegrityError:
            raise WorkflowNameConflict(
                "a workflow with this project and name exists"
            ) from None

    def delete_workflow(self, workflow_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            model = session.get(DataFactoryWorkflowModel, str(workflow_id))
            if model is None:
                raise self._not_found(workflow_id)
            session.execute(delete(DataFactoryRunModel).where(
                DataFactoryRunModel.workflow_id == str(workflow_id)
            ))
            session.delete(model)

    def save_run(self, workflow_id: UUID, run: WorkflowRunRecord) -> WorkflowRunRecord:
        if run.workflow_id != str(workflow_id):
            raise WorkflowConflict("run workflow id does not match workflow")
        with self._session_factory() as session, session.begin():
            if session.get(DataFactoryWorkflowModel, str(workflow_id)) is None:
                raise self._not_found(workflow_id)
            session.add(DataFactoryRunModel(
                id=run.id, workflow_id=run.workflow_id, status=run.status,
                node_results=deepcopy(run.node_results), variables=deepcopy(run.variables),
                started_at=self._parse_utc(run.started_at),
                finished_at=self._parse_utc(run.finished_at) if run.finished_at else None,
            ))
        return deepcopy(run)

    def list_runs(self, workflow_id: UUID) -> list[WorkflowRunRecord]:
        with self._session_factory() as session:
            if session.get(DataFactoryWorkflowModel, str(workflow_id)) is None:
                raise self._not_found(workflow_id)
            query = select(DataFactoryRunModel).where(
                DataFactoryRunModel.workflow_id == str(workflow_id)
            ).order_by(DataFactoryRunModel.started_at.desc())
            return [self._run_record(item) for item in session.scalars(query)]

    @staticmethod
    def _workflow_record(model: DataFactoryWorkflowModel) -> WorkflowRecord:
        return WorkflowRecord(
            UUID(model.id), UUID(model.project_id), model.name, model.description,
            deepcopy(model.nodes), deepcopy(model.edges), deepcopy(model.variables),
            model.state_version, SqlDataFactoryRepository._utc(model.created_at),
            SqlDataFactoryRepository._utc(model.updated_at),
        )

    @staticmethod
    def _run_record(model: DataFactoryRunModel) -> WorkflowRunRecord:
        return WorkflowRunRecord(
            model.id, model.workflow_id, model.status, deepcopy(model.node_results),
            deepcopy(model.variables), SqlDataFactoryRepository._utc(model.started_at).isoformat(),
            SqlDataFactoryRepository._utc(model.finished_at).isoformat()
            if model.finished_at else None,
        )

    @staticmethod
    def _not_found(workflow_id: UUID) -> WorkflowNotFound:
        return WorkflowNotFound(f"workflow {workflow_id} was not found")

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        return SqlDataFactoryRepository._utc(datetime.fromisoformat(value))

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


DataFactorySqlRepository = SqlDataFactoryRepository