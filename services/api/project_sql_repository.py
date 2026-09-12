from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .project_domain import ProjectRecord, ProjectStatus, validate_project_name
from .project_models import ProjectModel
from .project_repository import ProjectNameConflict, ProjectNotFound, ProjectVersionConflict


class SqlProjectRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_project(self, name: str, description: str) -> ProjectRecord:
        normalized = validate_project_name(name)
        project_id = uuid4()
        now = datetime.now(timezone.utc)
        record = ProjectRecord(
            project_id, normalized, description, ProjectStatus.ACTIVE, 0, now, now,
        )
        try:
            with self._session_factory() as session, session.begin():
                session.add(ProjectModel(
                    id=str(project_id), name=normalized, description=description,
                    status=record.status.value, state_version=0,
                    created_at=now, updated_at=now,
                ))
        except IntegrityError:
            raise ProjectNameConflict("a project with this name already exists") from None
        return record

    def get_project(self, project_id: UUID) -> ProjectRecord:
        with self._session_factory() as session:
            model = session.get(ProjectModel, str(project_id))
            if model is None:
                raise ProjectNotFound(f"project {project_id} was not found")
            return self._record(model)

    def list_projects(self, status: ProjectStatus | None = None) -> list[ProjectRecord]:
        with self._session_factory() as session:
            query = select(ProjectModel).order_by(ProjectModel.created_at)
            if status is not None:
                query = query.where(ProjectModel.status == status.value)
            return [self._record(item) for item in session.scalars(query)]

    def delete_project(self, project_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            model = session.get(ProjectModel, str(project_id))
            if model is None:
                raise ProjectNotFound(f"project {project_id} was not found")
            session.delete(model)

    def update_project(
        self,
        project_id: UUID,
        *,
        name: str,
        description: str,
        status: ProjectStatus,
        expected_version: int,
    ) -> ProjectRecord:
        normalized = validate_project_name(name)
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(ProjectModel).where(
                    ProjectModel.id == str(project_id)
                ).with_for_update())
                if model is None:
                    raise ProjectNotFound(f"project {project_id} was not found")
                current = self._record(model)
                if current.state_version != expected_version:
                    raise ProjectVersionConflict(
                        f"state version mismatch: expected {expected_version}, "
                        f"current {current.state_version}"
                    )
                updated = current.update(normalized, description, status)
                model.name = updated.name
                model.description = updated.description
                model.status = updated.status.value
                model.state_version = updated.state_version
                model.updated_at = updated.updated_at
                return updated
        except IntegrityError:
            raise ProjectNameConflict("a project with this name already exists") from None

    @staticmethod
    def _record(model: ProjectModel) -> ProjectRecord:
        return ProjectRecord(
            UUID(model.id), model.name, model.description,
            ProjectStatus(model.status), model.state_version,
            SqlProjectRepository._utc(model.created_at),
            SqlProjectRepository._utc(model.updated_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
