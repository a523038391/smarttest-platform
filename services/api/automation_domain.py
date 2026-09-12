from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from packages.protocol import Engine

from .quality_domain import InvalidAssetTransition


class AutomationScriptStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


SCRIPT_TRANSITIONS = {
    AutomationScriptStatus.DRAFT: {AutomationScriptStatus.ACTIVE},
    AutomationScriptStatus.ACTIVE: {AutomationScriptStatus.ARCHIVED},
    AutomationScriptStatus.ARCHIVED: {AutomationScriptStatus.ACTIVE},
}


def _next_script_status(
    current: AutomationScriptStatus, target: AutomationScriptStatus
) -> AutomationScriptStatus:
    if current == target:
        if current is AutomationScriptStatus.ARCHIVED:
            raise InvalidAssetTransition(
                "cannot modify a script while it is archived"
            )
        return current
    if target not in SCRIPT_TRANSITIONS.get(current, set()):
        raise InvalidAssetTransition(
            f"cannot transition script from {current.value} to {target.value}"
        )
    return target


@dataclass(frozen=True, slots=True)
class AutomationScriptRecord:
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

    def update(
        self,
        *,
        name: str,
        description: str,
        entrypoint: str,
        source_ref: str,
        content_digest: str,
        timeout_seconds: int,
        status: AutomationScriptStatus,
    ) -> "AutomationScriptRecord":
        return replace(
            self,
            name=name,
            description=description,
            entrypoint=entrypoint,
            source_ref=source_ref,
            content_digest=content_digest,
            timeout_seconds=timeout_seconds,
            status=_next_script_status(self.status, status),
            revision=self.revision + 1,
            state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class TestCaseScriptLinkRecord:
    test_case_id: UUID
    script_id: UUID
    created_at: datetime