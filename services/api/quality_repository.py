from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4

from .quality_domain import (
    CaseStepRecord,
    RequirementRecord,
    RequirementStatus,
    TestCasePriority,
    TestCaseRecord,
    TestCaseSource,
    TestCaseStatus,
    TraceLinkRecord,
)


class RequirementNotFound(LookupError):
    pass


class TestCaseNotFound(LookupError):
    pass


class AssetVersionConflict(ValueError):
    pass


class TraceLinkConflict(ValueError):
    pass


class QualityRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._requirements: dict[UUID, RequirementRecord] = {}
        self._requirement_revisions: dict[UUID, list[RequirementRecord]] = {}
        self._test_cases: dict[UUID, TestCaseRecord] = {}
        self._test_case_revisions: dict[UUID, list[TestCaseRecord]] = {}
        self._links: dict[tuple[UUID, UUID], TraceLinkRecord] = {}

    def create_requirement(
        self, project_id: UUID, title: str, description: str
    ) -> RequirementRecord:
        now = datetime.now(timezone.utc)
        record = RequirementRecord(
            uuid4(), project_id, title, description, RequirementStatus.DRAFT,
            1, 0, now, now,
        )
        with self._lock:
            self._requirements[record.id] = record
            self._requirement_revisions[record.id] = [record]
        return deepcopy(record)

    def get_requirement(self, requirement_id: UUID) -> RequirementRecord:
        with self._lock:
            try:
                return deepcopy(self._requirements[requirement_id])
            except KeyError as exc:
                raise RequirementNotFound(
                    f"requirement {requirement_id} was not found"
                ) from exc

    def list_requirements(self, project_id: UUID | None = None) -> list[RequirementRecord]:
        with self._lock:
            records = self._requirements.values()
            if project_id is not None:
                records = (item for item in records if item.project_id == project_id)
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def update_requirement(
        self,
        requirement_id: UUID,
        title: str,
        description: str,
        status: RequirementStatus,
        expected_version: int,
    ) -> RequirementRecord:
        with self._lock:
            current = self.get_requirement(requirement_id)
            self._check_version(current.state_version, expected_version)
            updated = current.update(title, description, status)
            self._requirements[requirement_id] = updated
            self._requirement_revisions[requirement_id].append(updated)
            return deepcopy(updated)

    def list_requirement_revisions(self, requirement_id: UUID) -> list[RequirementRecord]:
        with self._lock:
            self.get_requirement(requirement_id)
            return deepcopy(self._requirement_revisions[requirement_id])

    def create_test_case(
        self,
        project_id: UUID,
        title: str,
        description: str,
        preconditions: str,
        priority: TestCasePriority,
        steps: tuple[CaseStepRecord, ...],
        source: TestCaseSource = TestCaseSource.MANUAL,
    ) -> TestCaseRecord:
        now = datetime.now(timezone.utc)
        record = TestCaseRecord(
            uuid4(), project_id, title, description, preconditions, priority,
            TestCaseStatus.DRAFT, source, steps, 1, 0, now, now,
        )
        with self._lock:
            self._test_cases[record.id] = record
            self._test_case_revisions[record.id] = [record]
        return deepcopy(record)

    def get_test_case(self, test_case_id: UUID) -> TestCaseRecord:
        with self._lock:
            try:
                return deepcopy(self._test_cases[test_case_id])
            except KeyError as exc:
                raise TestCaseNotFound(
                    f"test case {test_case_id} was not found"
                ) from exc

    def list_test_cases(self, project_id: UUID | None = None) -> list[TestCaseRecord]:
        with self._lock:
            records = self._test_cases.values()
            if project_id is not None:
                records = (item for item in records if item.project_id == project_id)
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def update_test_case(
        self, test_case_id: UUID, *, expected_version: int, **changes: object
    ) -> TestCaseRecord:
        with self._lock:
            current = self.get_test_case(test_case_id)
            self._check_version(current.state_version, expected_version)
            updated = current.update(**changes)  # type: ignore[arg-type]
            self._test_cases[test_case_id] = updated
            self._test_case_revisions[test_case_id].append(updated)
            return deepcopy(updated)

    def list_test_case_revisions(self, test_case_id: UUID) -> list[TestCaseRecord]:
        with self._lock:
            self.get_test_case(test_case_id)
            return deepcopy(self._test_case_revisions[test_case_id])

    def link_requirement_test_case(
        self, requirement_id: UUID, test_case_id: UUID
    ) -> tuple[TraceLinkRecord, bool]:
        with self._lock:
            requirement = self.get_requirement(requirement_id)
            test_case = self.get_test_case(test_case_id)
            if requirement.project_id != test_case.project_id:
                raise TraceLinkConflict("requirement and test case belong to different projects")
            key = (requirement_id, test_case_id)
            if key in self._links:
                return deepcopy(self._links[key]), True
            link = TraceLinkRecord(requirement_id, test_case_id, datetime.now(timezone.utc))
            self._links[key] = link
            return deepcopy(link), False

    def list_requirement_test_cases(self, requirement_id: UUID) -> list[TestCaseRecord]:
        with self._lock:
            self.get_requirement(requirement_id)
            ids = [case_id for req_id, case_id in self._links if req_id == requirement_id]
            records = [self._test_cases[item] for item in ids]
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    @staticmethod
    def _check_version(current: int, expected: int) -> None:
        if current != expected:
            raise AssetVersionConflict(
                f"state version mismatch: expected {expected}, current {current}"
            )