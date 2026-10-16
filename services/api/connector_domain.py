from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from .environment_domain import EncryptedSecret


class ConnectorKind(str, Enum):
    MCP = "MCP"
    HTTP = "HTTP"


class ConnectorAuthType(str, Enum):
    NONE = "NONE"
    BEARER = "BEARER"
    API_KEY = "API_KEY"


@dataclass(frozen=True, slots=True)
class ConnectorTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConnectorRecord:
    id: UUID
    name: str
    description: str
    kind: ConnectorKind
    base_url: str
    auth_type: ConnectorAuthType
    auth_header: str
    enabled: bool
    timeout_seconds: int
    state_version: int
    secret_revision: int = field(repr=False)
    credential: EncryptedSecret | None = field(default=None, repr=False)
    tools: tuple[ConnectorTool, ...] = ()
    last_tested_at: datetime | None = None
    last_test_success: bool | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def credential_configured(self) -> bool:
        return self.credential is not None

    def update(self, **changes: Any) -> "ConnectorRecord":
        return replace(
            self, **changes, state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )