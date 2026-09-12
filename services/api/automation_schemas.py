from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

from packages.protocol import Engine
from pydantic import Field, field_validator
from services.source_store import MAX_SOURCE_BYTES, StoredSource

from .automation_domain import (
    AutomationScriptRecord,
    AutomationScriptStatus,
    TestCaseScriptLinkRecord,
)
from .schemas import ApiModel


class AutomationSourceCreate(ApiModel):
    project_id: UUID
    content: str = Field(max_length=MAX_SOURCE_BYTES, repr=False)

    @field_validator("content")
    @classmethod
    def validate_content_size(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("content must be valid UTF-8") from None
        if size > MAX_SOURCE_BYTES:
            raise ValueError("content cannot exceed 1 MiB when UTF-8 encoded")
        return value


class AutomationGitSourceCreate(ApiModel):
    project_id: UUID
    repository_url: str = Field(min_length=1, max_length=2_048)
    git_ref: str = Field(default="HEAD", max_length=255)

    @field_validator("repository_url", "git_ref")
    @classmethod
    def validate_git_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Git source fields must not contain padding or controls")
        return value


class AutomationHostSourceCreate(ApiModel):
    project_id: UUID
    project_directory: str = Field(min_length=1, max_length=2_048)
    python_executable: str = Field(min_length=1, max_length=2_048)

    @field_validator("project_directory", "python_executable")
    @classmethod
    def validate_host_path(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("host paths must not contain padding or controls")
        return value


class AutomationSourceResponse(ApiModel):
    source_ref: str
    content_digest: str
    size_bytes: int

    @classmethod
    def from_record(cls, record: StoredSource) -> "AutomationSourceResponse":
        return cls.model_validate(record, from_attributes=True)


class _ScriptBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    entrypoint: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=512)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("entrypoint must not contain whitespace padding or controls")
        path = PurePosixPath(value)
        if (
            "\\" in value or path.is_absolute() or ".." in path.parts
            or "." in path.parts or str(path) != value
            or any(":" in part for part in path.parts)
        ):
            raise ValueError("entrypoint must be a safe relative POSIX path")
        if value in {".", ""} or value.endswith("/"):
            raise ValueError("entrypoint must identify a file")
        return value

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("source_ref must not be blank or padded")
        if any(ord(character) < 32 for character in value):
            raise ValueError("source_ref must not contain control characters")
        return value


class AutomationScriptCreate(_ScriptBody):
    project_id: UUID
    engine: Engine


class AutomationScriptUpdate(_ScriptBody):
    status: AutomationScriptStatus
    state_version: int = Field(ge=0)


class AutomationScriptResponse(ApiModel):
    id: UUID
    project_id: UUID
    name: str
    description: str
    engine: Engine
    entrypoint: str
    source_ref: str
    content_digest: str
    timeout_seconds: int
    status: AutomationScriptStatus
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(
        cls, record: AutomationScriptRecord
    ) -> "AutomationScriptResponse":
        return cls.model_validate(record, from_attributes=True)


class AutomationScriptListResponse(ApiModel):
    items: list[AutomationScriptResponse]
    total: int


class TestCaseScriptLinkCreate(ApiModel):
    script_id: UUID


class TestCaseScriptLinkResponse(ApiModel):
    test_case_id: UUID
    script_id: UUID
    created_at: datetime

    @classmethod
    def from_record(
        cls, record: TestCaseScriptLinkRecord
    ) -> "TestCaseScriptLinkResponse":
        return cls.model_validate(record, from_attributes=True)