from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from packages.protocol import SecretReference
from services.secret_broker import (
    ACTIVE_SECRET_STATES,
    CapabilityRejected,
    SecretGrantBinding,
    SecretGrantRecord,
)

from .models import AttemptModel
from .secret_grant_models import SecretCapabilityGrantModel


class SqlSecretGrantRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def issue(self, record: SecretGrantRecord, now: datetime) -> SecretGrantRecord:
        try:
            with self._session_factory() as session, session.begin():
                attempt = self._valid_attempt(session, record.binding, now)
                expires_at = min(record.expires_at, self._utc(attempt.lease_expires_at))
                if expires_at <= now:
                    raise CapabilityRejected()
                stored = SecretGrantRecord(
                    id=record.id, token_digest=record.token_digest,
                    binding=record.binding, issued_at=record.issued_at,
                    expires_at=expires_at,
                )
                session.add(self._to_model(stored))
                session.flush()
                return stored
        except CapabilityRejected:
            raise
        except Exception:
            raise CapabilityRejected() from None

    def consume(
        self, token_digest: str, binding: SecretGrantBinding, now: datetime
    ) -> SecretGrantRecord:
        try:
            with self._session_factory() as session, session.begin():
                self._valid_attempt(session, binding, now)
                statement = update(SecretCapabilityGrantModel).where(
                    SecretCapabilityGrantModel.token_digest == token_digest,
                    SecretCapabilityGrantModel.task_id == str(binding.task_id),
                    SecretCapabilityGrantModel.tenant_id == str(binding.tenant_id),
                    SecretCapabilityGrantModel.project_id == str(binding.project_id),
                    SecretCapabilityGrantModel.run_id == str(binding.run_id),
                    SecretCapabilityGrantModel.attempt_id == str(binding.attempt_id),
                    SecretCapabilityGrantModel.runner_id == binding.runner_id,
                    SecretCapabilityGrantModel.environment_id == str(binding.environment_id),
                    SecretCapabilityGrantModel.environment_revision == binding.environment_revision,
                    SecretCapabilityGrantModel.reference_manifest == self._manifest(binding),
                    SecretCapabilityGrantModel.expires_at > now,
                    SecretCapabilityGrantModel.consumed_at.is_(None),
                    SecretCapabilityGrantModel.revoked_at.is_(None),
                ).values(consumed_at=now)
                result = session.execute(statement)
                if result.rowcount != 1:
                    raise CapabilityRejected()
                model = session.scalar(select(SecretCapabilityGrantModel).where(
                    SecretCapabilityGrantModel.token_digest == token_digest
                ))
                if model is None:
                    raise CapabilityRejected()
                return self._to_record(model)
        except CapabilityRejected:
            raise
        except Exception:
            raise CapabilityRejected() from None

    def revoke(self, token_digest: str, now: datetime) -> None:
        try:
            with self._session_factory() as session, session.begin():
                session.execute(
                    update(SecretCapabilityGrantModel).where(
                        SecretCapabilityGrantModel.token_digest == token_digest,
                        SecretCapabilityGrantModel.consumed_at.is_(None),
                        SecretCapabilityGrantModel.revoked_at.is_(None),
                    ).values(revoked_at=now)
                )
        except Exception:
            raise CapabilityRejected() from None

    def revoke_attempt(self, attempt_id: UUID, now: datetime) -> int:
        try:
            with self._session_factory() as session, session.begin():
                result = session.execute(
                    update(SecretCapabilityGrantModel).where(
                        SecretCapabilityGrantModel.attempt_id == str(attempt_id),
                        SecretCapabilityGrantModel.consumed_at.is_(None),
                        SecretCapabilityGrantModel.revoked_at.is_(None),
                    ).values(revoked_at=now)
                )
                return result.rowcount or 0
        except Exception:
            raise CapabilityRejected() from None

    @staticmethod
    def _valid_attempt(
        session: Session, binding: SecretGrantBinding, now: datetime
    ) -> AttemptModel:
        attempt = session.scalar(select(AttemptModel).where(
            AttemptModel.id == str(binding.attempt_id)
        ).with_for_update())
        if (
            attempt is None or attempt.run_id != str(binding.run_id)
            or attempt.runner_id != binding.runner_id
            or attempt.state not in {state.value for state in ACTIVE_SECRET_STATES}
            or attempt.lease_expires_at is None
            or SqlSecretGrantRepository._utc(attempt.lease_expires_at) <= now
        ):
            raise CapabilityRejected()
        return attempt

    @staticmethod
    def _manifest(binding: SecretGrantBinding) -> list[dict[str, str]]:
        return [
            {
                "category": item.category,
                "name": item.name,
                "secret_ref": str(item.secret_ref),
            }
            for item in binding.secret_references
        ]

    @classmethod
    def _to_model(cls, record: SecretGrantRecord) -> SecretCapabilityGrantModel:
        binding = record.binding
        return SecretCapabilityGrantModel(
            id=str(record.id), token_digest=record.token_digest,
            task_id=str(binding.task_id), tenant_id=str(binding.tenant_id),
            project_id=str(binding.project_id), run_id=str(binding.run_id),
            attempt_id=str(binding.attempt_id), runner_id=binding.runner_id,
            environment_id=str(binding.environment_id),
            environment_revision=binding.environment_revision,
            reference_manifest=cls._manifest(binding), issued_at=record.issued_at,
            expires_at=record.expires_at, consumed_at=record.consumed_at,
            revoked_at=record.revoked_at,
        )

    @classmethod
    def _to_record(cls, model: SecretCapabilityGrantModel) -> SecretGrantRecord:
        try:
            references = tuple(
                SecretReference.model_validate(item) for item in model.reference_manifest
            )
            binding = SecretGrantBinding(
                task_id=UUID(model.task_id), tenant_id=UUID(model.tenant_id),
                project_id=UUID(model.project_id), run_id=UUID(model.run_id),
                attempt_id=UUID(model.attempt_id), runner_id=model.runner_id,
                environment_id=UUID(model.environment_id),
                environment_revision=model.environment_revision,
                secret_references=references,
            )
            return SecretGrantRecord(
                id=UUID(model.id), token_digest=model.token_digest, binding=binding,
                issued_at=cls._utc(model.issued_at), expires_at=cls._utc(model.expires_at),
                consumed_at=cls._utc(model.consumed_at) if model.consumed_at else None,
                revoked_at=cls._utc(model.revoked_at) if model.revoked_at else None,
            )
        except Exception:
            raise CapabilityRejected() from None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


SqlSecretCapabilityGrantRepository = SqlSecretGrantRepository
SqlSecretGrantStore = SqlSecretGrantRepository