from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .quality_domain import (
    CaseStepRecord, RequirementRecord, RequirementStatus, TestCasePriority,
    TestCaseRecord, TestCaseSource, TestCaseStatus, TraceLinkRecord,
)
from .quality_models import (
    RequirementModel, RequirementRevisionModel, TestCaseModel,
    TestCaseRevisionModel, TraceLinkModel,
)
from .quality_repository import (
    AssetVersionConflict, RequirementNotFound, TestCaseNotFound, TraceLinkConflict,
)


class SqlQualityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_requirement(
        self, project_id: UUID, title: str, description: str
    ) -> RequirementRecord:
        now = datetime.now(timezone.utc)
        record = RequirementRecord(
            uuid4(), project_id, title, description, RequirementStatus.DRAFT,
            1, 0, now, now,
        )
        with self._session_factory() as session, session.begin():
            session.add(RequirementModel(
                id=str(record.id), project_id=str(project_id), title=title,
                description=description, status=record.status.value,
                current_revision=1, state_version=0, created_at=now, updated_at=now,
            ))
            session.add(self._requirement_revision(record))
        return record

    def get_requirement(self, requirement_id: UUID) -> RequirementRecord:
        with self._session_factory() as session:
            model = session.get(RequirementModel, str(requirement_id))
            if model is None:
                raise RequirementNotFound(f"requirement {requirement_id} was not found")
            return self._requirement_record(model)

    def list_requirements(self, project_id: UUID | None = None) -> list[RequirementRecord]:
        with self._session_factory() as session:
            query = select(RequirementModel).order_by(RequirementModel.created_at)
            if project_id is not None:
                query = query.where(RequirementModel.project_id == str(project_id))
            return [self._requirement_record(item) for item in session.scalars(query)]

    def update_requirement(
        self, requirement_id: UUID, title: str, description: str,
        status: RequirementStatus, expected_version: int,
    ) -> RequirementRecord:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(RequirementModel).where(
                RequirementModel.id == str(requirement_id)
            ).with_for_update())
            if model is None:
                raise RequirementNotFound(f"requirement {requirement_id} was not found")
            current = self._requirement_record(model)
            self._check_version(current.state_version, expected_version)
            updated = current.update(title, description, status)
            model.title, model.description, model.status = title, description, status.value
            model.current_revision = updated.revision
            model.state_version = updated.state_version
            model.updated_at = updated.updated_at
            session.add(self._requirement_revision(updated))
            return updated

    def list_requirement_revisions(self, requirement_id: UUID) -> list[RequirementRecord]:
        current = self.get_requirement(requirement_id)
        with self._session_factory() as session:
            models = session.scalars(select(RequirementRevisionModel).where(
                RequirementRevisionModel.requirement_id == str(requirement_id)
            ).order_by(RequirementRevisionModel.revision)).all()
            return [RequirementRecord(
                requirement_id, current.project_id, item.title, item.description,
                RequirementStatus(item.status), item.revision, item.revision - 1,
                current.created_at, self._utc(item.created_at),
            ) for item in models]

    def create_test_case(
        self, project_id: UUID, title: str, description: str, preconditions: str,
        priority: TestCasePriority, steps: tuple[CaseStepRecord, ...],
        source: TestCaseSource = TestCaseSource.MANUAL,
    ) -> TestCaseRecord:
        now = datetime.now(timezone.utc)
        record = TestCaseRecord(
            uuid4(), project_id, title, description, preconditions, priority,
            TestCaseStatus.DRAFT, source, steps, 1, 0, now, now,
        )
        values = self._case_values(record)
        with self._session_factory() as session, session.begin():
            session.add(TestCaseModel(
                id=str(record.id), project_id=str(project_id), **values,
                current_revision=1, state_version=0, created_at=now, updated_at=now,
            ))
            session.add(TestCaseRevisionModel(
                test_case_id=str(record.id), revision=record.revision,
                **values, created_at=now,
            ))
        return record

    def get_test_case(self, test_case_id: UUID) -> TestCaseRecord:
        with self._session_factory() as session:
            model = session.get(TestCaseModel, str(test_case_id))
            if model is None:
                raise TestCaseNotFound(f"test case {test_case_id} was not found")
            return self._case_record(model)

    def list_test_cases(self, project_id: UUID | None = None) -> list[TestCaseRecord]:
        with self._session_factory() as session:
            query = select(TestCaseModel).order_by(TestCaseModel.created_at)
            if project_id is not None:
                query = query.where(TestCaseModel.project_id == str(project_id))
            return [self._case_record(item) for item in session.scalars(query)]

    def update_test_case(
        self, test_case_id: UUID, *, expected_version: int, **changes: object
    ) -> TestCaseRecord:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(TestCaseModel).where(
                TestCaseModel.id == str(test_case_id)
            ).with_for_update())
            if model is None:
                raise TestCaseNotFound(f"test case {test_case_id} was not found")
            current = self._case_record(model)
            self._check_version(current.state_version, expected_version)
            updated = current.update(**changes)  # type: ignore[arg-type]
            values = self._case_values(updated)
            for key, value in values.items():
                setattr(model, key, value)
            model.current_revision = updated.revision
            model.state_version = updated.state_version
            model.updated_at = updated.updated_at
            session.add(TestCaseRevisionModel(
                test_case_id=str(test_case_id), revision=updated.revision,
                **values, created_at=updated.updated_at,
            ))
            return updated

    def list_test_case_revisions(self, test_case_id: UUID) -> list[TestCaseRecord]:
        current = self.get_test_case(test_case_id)
        with self._session_factory() as session:
            models = session.scalars(select(TestCaseRevisionModel).where(
                TestCaseRevisionModel.test_case_id == str(test_case_id)
            ).order_by(TestCaseRevisionModel.revision)).all()
            return [self._case_revision_record(current, item) for item in models]

    def link_requirement_test_case(
        self, requirement_id: UUID, test_case_id: UUID
    ) -> tuple[TraceLinkRecord, bool]:
        key = (str(requirement_id), str(test_case_id))
        try:
            with self._session_factory() as session, session.begin():
                requirement = session.get(RequirementModel, key[0])
                if requirement is None:
                    raise RequirementNotFound(f"requirement {requirement_id} was not found")
                test_case = session.get(TestCaseModel, key[1])
                if test_case is None:
                    raise TestCaseNotFound(f"test case {test_case_id} was not found")
                if requirement.project_id != test_case.project_id:
                    raise TraceLinkConflict(
                        "requirement and test case belong to different projects"
                    )
                existing = session.get(TraceLinkModel, key)
                if existing is not None:
                    return self._link_record(existing), True
                now = datetime.now(timezone.utc)
                session.add(TraceLinkModel(
                    requirement_id=key[0], test_case_id=key[1], created_at=now
                ))
                session.flush()
                return TraceLinkRecord(requirement_id, test_case_id, now), False
        except IntegrityError as exc:
            with self._session_factory() as session:
                existing = session.get(TraceLinkModel, key)
                if existing is not None:
                    return self._link_record(existing), True
            raise TraceLinkConflict(
                "requirement or test case changed while creating the link"
            ) from exc

    def list_requirement_test_cases(self, requirement_id: UUID) -> list[TestCaseRecord]:
        self.get_requirement(requirement_id)
        with self._session_factory() as session:
            query = (select(TestCaseModel).join(
                TraceLinkModel, TraceLinkModel.test_case_id == TestCaseModel.id
            ).where(TraceLinkModel.requirement_id == str(requirement_id)).order_by(
                TestCaseModel.created_at
            ))
            return [self._case_record(item) for item in session.scalars(query)]

    @staticmethod
    def _requirement_revision(record: RequirementRecord) -> RequirementRevisionModel:
        return RequirementRevisionModel(
            requirement_id=str(record.id), revision=record.revision, title=record.title,
            description=record.description, status=record.status.value,
            created_at=record.updated_at,
        )

    @staticmethod
    def _requirement_record(model: RequirementModel) -> RequirementRecord:
        return RequirementRecord(
            UUID(model.id), UUID(model.project_id), model.title, model.description,
            RequirementStatus(model.status), model.current_revision, model.state_version,
            SqlQualityRepository._utc(model.created_at), SqlQualityRepository._utc(model.updated_at),
        )

    @staticmethod
    def _case_values(record: TestCaseRecord) -> dict[str, object]:
        return {
            "title": record.title, "description": record.description,
            "preconditions": record.preconditions, "priority": record.priority.value,
            "status": record.status.value, "source": record.source.value,
            "steps": [
                {"order": step.order, "action": step.action,
                 "expected_result": step.expected_result} for step in record.steps
            ],
        }

    @staticmethod
    def _case_record(model: TestCaseModel) -> TestCaseRecord:
        return TestCaseRecord(
            UUID(model.id), UUID(model.project_id), model.title, model.description,
            model.preconditions, TestCasePriority(model.priority), TestCaseStatus(model.status),
            TestCaseSource(model.source), SqlQualityRepository._steps(model.steps),
            model.current_revision, model.state_version,
            SqlQualityRepository._utc(model.created_at), SqlQualityRepository._utc(model.updated_at),
        )

    @staticmethod
    def _case_revision_record(current: TestCaseRecord, model: TestCaseRevisionModel) -> TestCaseRecord:
        return TestCaseRecord(
            current.id, current.project_id, model.title, model.description,
            model.preconditions, TestCasePriority(model.priority), TestCaseStatus(model.status),
            TestCaseSource(model.source), SqlQualityRepository._steps(model.steps),
            model.revision, model.revision - 1, current.created_at,
            SqlQualityRepository._utc(model.created_at),
        )

    @staticmethod
    def _steps(values: list[dict[str, object]]) -> tuple[CaseStepRecord, ...]:
        return tuple(CaseStepRecord(
            int(item["order"]), str(item["action"]), str(item["expected_result"])
        ) for item in values)

    @staticmethod
    def _link_record(model: TraceLinkModel) -> TraceLinkRecord:
        return TraceLinkRecord(
            UUID(model.requirement_id), UUID(model.test_case_id),
            SqlQualityRepository._utc(model.created_at),
        )

    @staticmethod
    def _check_version(current: int, expected: int) -> None:
        if current != expected:
            raise AssetVersionConflict(
                f"state version mismatch: expected {expected}, current {current}"
            )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)