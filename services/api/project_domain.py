from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID


class ProjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


PROJECT_TRANSITIONS = {
    ProjectStatus.ACTIVE: {ProjectStatus.ARCHIVED},
}


class InvalidProjectTransition(ValueError):
    pass


def next_project_status(current: ProjectStatus, target: ProjectStatus) -> ProjectStatus:
    if current == target:
        if current not in PROJECT_TRANSITIONS:
            raise InvalidProjectTransition(
                f"cannot modify a project in terminal state {current.value}"
            )
        return current
    if target not in PROJECT_TRANSITIONS.get(current, set()):
        raise InvalidProjectTransition(
            f"cannot transition from {current.value} to {target.value}"
        )
    return target


def validate_project_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("project name must contain 1 to 255 characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: UUID
    name: str
    description: str
    status: ProjectStatus
    state_version: int
    created_at: datetime
    updated_at: datetime

    def update(
        self, name: str, description: str, status: ProjectStatus
    ) -> "ProjectRecord":
        next_status = next_project_status(self.status, status)
        return replace(
            self,
            name=name,
            description=description,
            status=next_status,
            state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )
