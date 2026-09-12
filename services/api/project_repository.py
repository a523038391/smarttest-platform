from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4

from .project_domain import ProjectRecord, ProjectStatus, validate_project_name


class ProjectNotFound(LookupError):
    pass


class ProjectNameConflict(ValueError):
    pass


class ProjectVersionConflict(ValueError):
    pass


class ProjectInUse(ValueError):
    pass


class ProjectRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._projects: dict[UUID, ProjectRecord] = {}
        self._names: dict[str, UUID] = {}

    def create_project(self, name: str, description: str) -> ProjectRecord:
        normalized = validate_project_name(name)
        project_id = uuid4()
        with self._lock:
            if normalized in self._names:
                raise ProjectNameConflict("a project with this name already exists")
            now = datetime.now(timezone.utc)
            record = ProjectRecord(
                project_id, normalized, description, ProjectStatus.ACTIVE, 0, now, now,
            )
            self._projects[project_id] = record
            self._names[normalized] = project_id
            return deepcopy(record)

    def get_project(self, project_id: UUID) -> ProjectRecord:
        with self._lock:
            try:
                return deepcopy(self._projects[project_id])
            except KeyError:
                raise ProjectNotFound(f"project {project_id} was not found") from None

    def list_projects(self, status: ProjectStatus | None = None) -> list[ProjectRecord]:
        with self._lock:
            records = self._projects.values()
            if status is not None:
                records = [item for item in records if item.status == status]
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def delete_project(self, project_id: UUID) -> None:
        with self._lock:
            current = self.get_project(project_id)
            del self._projects[project_id]
            self._names.pop(current.name, None)

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
        with self._lock:
            current = self.get_project(project_id)
            if current.state_version != expected_version:
                raise ProjectVersionConflict(
                    f"state version mismatch: expected {expected_version}, "
                    f"current {current.state_version}"
                )
            owner = self._names.get(normalized)
            if owner is not None and owner != project_id:
                raise ProjectNameConflict("a project with this name already exists")
            updated = current.update(normalized, description, status)
            self._projects[project_id] = updated
            if normalized != current.name:
                self._names.pop(current.name, None)
                self._names[normalized] = project_id
            return deepcopy(updated)
