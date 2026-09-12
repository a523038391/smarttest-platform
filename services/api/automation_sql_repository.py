from datetime import datetime, timezone
from uuid import UUID, uuid4

from packages.protocol import Engine
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .automation_domain import (
    AutomationScriptRecord,
    AutomationScriptStatus,
    TestCaseScriptLinkRecord,
)
from .automation_models import (
    AutomationScriptModel,
    AutomationScriptRevisionModel,
    TestCaseScriptLinkModel,
)
from .automation_repository import ScriptLinkConflict, ScriptNotFound
from .quality_domain import TestCaseStatus
from .quality_models import TestCaseModel
from .quality_repository import AssetVersionConflict, TestCaseNotFound


class SqlAutomationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

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
        values = self._values(record)
        with self._session_factory() as session, session.begin():
            session.add(AutomationScriptModel(
                id=str(record.id), project_id=str(project_id), engine=engine.value,
                **values, current_revision=1, state_version=0,
                created_at=now, updated_at=now,
            ))
            session.add(AutomationScriptRevisionModel(
                script_id=str(record.id), revision=1, **values, created_at=now,
            ))
        return record

    def get_script(self, script_id: UUID) -> AutomationScriptRecord:
        with self._session_factory() as session:
            model = session.get(AutomationScriptModel, str(script_id))
            if model is None:
                raise ScriptNotFound(f"automation script {script_id} was not found")
            return self._record(model)

    def list_scripts(self, project_id: UUID) -> list[AutomationScriptRecord]:
        with self._session_factory() as session:
            query = select(AutomationScriptModel).where(
                AutomationScriptModel.project_id == str(project_id)
            ).order_by(AutomationScriptModel.created_at)
            return [self._record(item) for item in session.scalars(query)]

    def update_script(
        self, script_id: UUID, *, expected_version: int, **changes: object
    ) -> AutomationScriptRecord:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(AutomationScriptModel).where(
                AutomationScriptModel.id == str(script_id)
            ).with_for_update())
            if model is None:
                raise ScriptNotFound(f"automation script {script_id} was not found")
            current = self._record(model)
            if current.state_version != expected_version:
                raise AssetVersionConflict(
                    f"state version mismatch: expected {expected_version}, "
                    f"current {current.state_version}"
                )
            updated = current.update(**changes)  # type: ignore[arg-type]
            values = self._values(updated)
            for key, value in values.items():
                setattr(model, key, value)
            model.current_revision = updated.revision
            model.state_version = updated.state_version
            model.updated_at = updated.updated_at
            session.add(AutomationScriptRevisionModel(
                script_id=str(script_id), revision=updated.revision,
                **values, created_at=updated.updated_at,
            ))
            return updated

    def list_revisions(self, script_id: UUID) -> list[AutomationScriptRecord]:
        current = self.get_script(script_id)
        with self._session_factory() as session:
            query = select(AutomationScriptRevisionModel).where(
                AutomationScriptRevisionModel.script_id == str(script_id)
            ).order_by(AutomationScriptRevisionModel.revision)
            return [self._revision_record(current, item) for item in session.scalars(query)]

    def get_revision(self, script_id: UUID, revision: int) -> AutomationScriptRecord:
        current = self.get_script(script_id)
        with self._session_factory() as session:
            model = session.get(
                AutomationScriptRevisionModel, (str(script_id), revision)
            )
            if model is None:
                raise ScriptNotFound(
                    f"automation script {script_id} revision {revision} was not found"
                )
            return self._revision_record(current, model)

    def link_test_case(
        self, test_case_id: UUID, script_id: UUID
    ) -> tuple[TestCaseScriptLinkRecord, bool]:
        key = (str(test_case_id), str(script_id))
        try:
            with self._session_factory() as session, session.begin():
                test_case = session.scalar(select(TestCaseModel).where(
                    TestCaseModel.id == key[0]
                ).with_for_update())
                if test_case is None:
                    raise TestCaseNotFound(f"test case {test_case_id} was not found")
                script = session.scalar(select(AutomationScriptModel).where(
                    AutomationScriptModel.id == key[1]
                ).with_for_update())
                if script is None:
                    raise ScriptNotFound(f"automation script {script_id} was not found")
                self._validate_project(test_case, script)
                existing = session.get(TestCaseScriptLinkModel, key)
                if existing is not None:
                    return self._link_record(existing), True
                self._validate_linkable(test_case, script)
                now = datetime.now(timezone.utc)
                session.add(TestCaseScriptLinkModel(
                    test_case_id=key[0], script_id=key[1], created_at=now
                ))
                session.flush()
                return TestCaseScriptLinkRecord(test_case_id, script_id, now), False
        except IntegrityError as exc:
            with self._session_factory() as session:
                existing = session.get(TestCaseScriptLinkModel, key)
                if existing is not None:
                    return self._link_record(existing), True
            raise ScriptLinkConflict(
                "test case or automation script changed while creating the link"
            ) from exc

    def list_test_case_scripts(self, test_case_id: UUID) -> list[AutomationScriptRecord]:
        with self._session_factory() as session:
            if session.get(TestCaseModel, str(test_case_id)) is None:
                raise TestCaseNotFound(f"test case {test_case_id} was not found")
            query = select(AutomationScriptModel).join(
                TestCaseScriptLinkModel,
                TestCaseScriptLinkModel.script_id == AutomationScriptModel.id,
            ).where(
                TestCaseScriptLinkModel.test_case_id == str(test_case_id)
            ).order_by(AutomationScriptModel.created_at)
            return [self._record(item) for item in session.scalars(query)]

    @staticmethod
    def _validate_project(
        test_case: TestCaseModel, script: AutomationScriptModel
    ) -> None:
        if test_case.project_id != script.project_id:
            raise ScriptLinkConflict(
                "test case and automation script belong to different projects"
            )

    @staticmethod
    def _validate_linkable(
        test_case: TestCaseModel, script: AutomationScriptModel
    ) -> None:
        if test_case.status == TestCaseStatus.ARCHIVED.value:
            raise ScriptLinkConflict("an archived test case cannot receive new script links")
        if script.status == AutomationScriptStatus.ARCHIVED.value:
            raise ScriptLinkConflict("an archived automation script cannot be linked")

    @staticmethod
    def _values(record: AutomationScriptRecord) -> dict[str, object]:
        return {
            "name": record.name,
            "description": record.description,
            "entrypoint": record.entrypoint,
            "source_ref": record.source_ref,
            "content_digest": record.content_digest,
            "timeout_seconds": record.timeout_seconds,
            "status": record.status.value,
        }

    @staticmethod
    def _record(model: AutomationScriptModel) -> AutomationScriptRecord:
        return AutomationScriptRecord(
            UUID(model.id), UUID(model.project_id), model.name, model.description,
            Engine(model.engine), model.entrypoint, model.source_ref,
            model.content_digest, model.timeout_seconds,
            AutomationScriptStatus(model.status), model.current_revision,
            model.state_version, SqlAutomationRepository._utc(model.created_at),
            SqlAutomationRepository._utc(model.updated_at),
        )

    @staticmethod
    def _revision_record(
        current: AutomationScriptRecord, model: AutomationScriptRevisionModel
    ) -> AutomationScriptRecord:
        return AutomationScriptRecord(
            current.id, current.project_id, model.name, model.description,
            current.engine, model.entrypoint, model.source_ref,
            model.content_digest, model.timeout_seconds,
            AutomationScriptStatus(model.status), model.revision,
            model.revision - 1, current.created_at,
            SqlAutomationRepository._utc(model.created_at),
        )

    @staticmethod
    def _link_record(model: TestCaseScriptLinkModel) -> TestCaseScriptLinkRecord:
        return TestCaseScriptLinkRecord(
            UUID(model.test_case_id), UUID(model.script_id),
            SqlAutomationRepository._utc(model.created_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)