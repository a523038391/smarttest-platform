from datetime import datetime
from uuid import UUID

from pydantic import Field

from .project_domain import ProjectRecord, ProjectStatus
from .schemas import ApiModel


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)


class ProjectUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    status: ProjectStatus
    state_version: int = Field(ge=0)


class ProjectResponse(ApiModel):
    id: UUID
    name: str
    description: str
    status: ProjectStatus
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ProjectRecord) -> "ProjectResponse":
        return cls.model_validate(record, from_attributes=True)


class ProjectListResponse(ApiModel):
    items: list[ProjectResponse]
    total: int
