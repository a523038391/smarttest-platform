from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .connector_domain import ConnectorAuthType, ConnectorKind, ConnectorRecord
from .schemas import ApiModel


_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class _ConnectorBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    kind: ConnectorKind
    base_url: str = Field(min_length=1, max_length=8_192)
    auth_type: ConnectorAuthType = ConnectorAuthType.NONE
    auth_header: str = Field(default="Authorization", min_length=1, max_length=255)
    credential: str | None = Field(default=None, max_length=65_536, repr=False)
    timeout_seconds: int = Field(default=10, ge=1, le=60)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("connector name must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("base_url must be an HTTP(S) URL without userinfo") from None
        return value

    @field_validator("auth_header")
    @classmethod
    def validate_auth_header(cls, value: str) -> str:
        if not _HEADER_NAME.fullmatch(value):
            raise ValueError("auth_header is not a valid HTTP header name")
        return value


class ConnectorCreate(_ConnectorBody):
    enabled: bool = True

    @model_validator(mode="after")
    def require_credential(self) -> "ConnectorCreate":
        if self.auth_type is not ConnectorAuthType.NONE and not self.credential:
            raise ValueError("credential is required for this auth type")
        return self


class ConnectorUpdate(_ConnectorBody):
    enabled: bool | None = None
    state_version: int = Field(ge=0)


class ConnectorEnabledUpdate(ApiModel):
    enabled: bool
    state_version: int = Field(ge=0)


class ConnectorToolResponse(ApiModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ConnectorResponse(ApiModel):
    id: UUID
    name: str
    description: str
    kind: ConnectorKind
    base_url: str
    auth_type: ConnectorAuthType
    auth_header: str
    credential_configured: bool
    enabled: bool
    timeout_seconds: int
    state_version: int
    tools: list[ConnectorToolResponse]
    last_tested_at: datetime | None
    last_test_success: bool | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ConnectorRecord) -> "ConnectorResponse":
        return cls(
            id=record.id, name=record.name, description=record.description,
            kind=record.kind, base_url=record.base_url, auth_type=record.auth_type,
            auth_header=record.auth_header,
            credential_configured=record.credential_configured,
            enabled=record.enabled, timeout_seconds=record.timeout_seconds,
            state_version=record.state_version,
            tools=[ConnectorToolResponse(
                name=item.name, description=item.description,
                input_schema=item.input_schema,
            ) for item in record.tools],
            last_tested_at=record.last_tested_at,
            last_test_success=record.last_test_success, last_error=record.last_error,
            created_at=record.created_at, updated_at=record.updated_at,
        )


class ConnectorListResponse(ApiModel):
    items: list[ConnectorResponse]
    total: int


class ConnectorTestResponse(ApiModel):
    connector: ConnectorResponse
    success: bool
    message: str
    latency_ms: float
    tool_count: int