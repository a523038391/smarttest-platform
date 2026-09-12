from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from packages.protocol import Engine

from .automation_domain import (
    AutomationScriptRecord,
    AutomationScriptStatus,
    TestCaseScriptLinkRecord,
)
from .quality_domain import TestCaseRecord, TestCaseStatus
from .quality_repository import AssetVersionConflict


class ScriptNotFound(LookupError):
    pass


class ScriptLinkConflict(ValueError):
    pass


class TestCaseReader(Protocol):
    def get_test_case(self, test_case_id: UUID) -> TestCaseRecord: ...


class AutomationRepository:
    def __init__(self, test_cases: TestCaseReader) -> None:
        self._test_cases = test_cases
        self._lock = RLock()
        self._scripts: dict[UUID, AutomationScriptRecord] = {}
        self._revisions: dict[UUID, list[AutomationScriptRecord]] = {}
        self._links: dict[tuple[UUID, UUID], TestCaseScriptLinkRecord] = {}

    def create_script(
        self,
        project_id: UUID,
        name: str,
        description: str,
        engine: Engine,
        entrypoint: str,
        source_ref: str,
        content_digest: str,
        timeout_seconds: int,
    ) -> AutomationScriptRecord:
        now = datetime.now(timezone.utc)
        record = AutomationScriptRecord(
            uuid4(), project_id, name, description, engine, entrypoint,
            source_ref, content_digest, timeout_seconds,
            AutomationScriptStatus.DRAFT, 1, 0, now, now,
        )
        with self._lock:
            self._scripts[record.id] = record
            self._revisions[record.id] = [record]
        return deepcopy(record)

    def get_script(self, script_id: UUID) -> AutomationScriptRecord:
        with self._lock:
            try:
                return deepcopy(self._scripts[script_id])
            except KeyError as exc:
                raise ScriptNotFound(f"automation script {script_id} was not found") from exc

    def list_scripts(self, project_id: UUID) -> list[AutomationScriptRecord]:
        with self._lock:
            records = (
                item for item in self._scripts.values()
                if item.project_id == project_id
            )
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def update_script(
        self, script_id: UUID, *, expected_version: int, **changes: object
    ) -> AutomationScriptRecord:
        with self._lock:
            current = self.get_script(script_id)
            if current.state_version != expected_version:
                raise AssetVersionConflict(
                    f"state version mismatch: expected {expected_version}, "
                    f"current {current.state_version}"
                )
            updated = current.update(**changes)  # type: ignore[arg-type]
            self._scripts[script_id] = updated
            self._revisions[script_id].append(updated)
            return deepcopy(updated)

    def list_revisions(self, script_id: UUID) -> list[AutomationScriptRecord]:
        with self._lock:
            self.get_script(script_id)
            return deepcopy(self._revisions[script_id])

    def get_revision(self, script_id: UUID, revision: int) -> AutomationScriptRecord:
        with self._lock:
            self.get_script(script_id)
            revisions = self._revisions[script_id]
            if revision < 1 or revision > len(revisions):
                raise ScriptNotFound(
                    f"automation script {script_id} revision {revision} was not found"
                )
            return deepcopy(revisions[revision - 1])

    def link_test_case(
        self, test_case_id: UUID, script_id: UUID
    ) -> tuple[TestCaseScriptLinkRecord, bool]:
        with self._lock:
            test_case = self._test_cases.get_test_case(test_case_id)
            script = self.get_script(script_id)
            key = (test_case_id, script_id)
            self._validate_project(test_case, script)
            if key in self._links:
                return deepcopy(self._links[key]), True
            self._validate_linkable(test_case, script)
            link = TestCaseScriptLinkRecord(
                test_case_id, script_id, datetime.now(timezone.utc)
            )
            self._links[key] = link
            return deepcopy(link), False

    def list_test_case_scripts(self, test_case_id: UUID) -> list[AutomationScriptRecord]:
        with self._lock:
            self._test_cases.get_test_case(test_case_id)
            scripts = [
                self._scripts[script_id]
                for case_id, script_id in self._links
                if case_id == test_case_id
            ]
            return deepcopy(sorted(scripts, key=lambda item: item.created_at))

    @staticmethod
    def _validate_project(
        test_case: TestCaseRecord, script: AutomationScriptRecord
    ) -> None:
        if test_case.project_id != script.project_id:
            raise ScriptLinkConflict(
                "test case and automation script belong to different projects"
            )

    @staticmethod
    def _validate_linkable(
        test_case: TestCaseRecord, script: AutomationScriptRecord
    ) -> None:
        if test_case.status is TestCaseStatus.ARCHIVED:
            raise ScriptLinkConflict("an archived test case cannot receive new script links")
        if script.status is AutomationScriptStatus.ARCHIVED:
            raise ScriptLinkConflict("an archived automation script cannot be linked")