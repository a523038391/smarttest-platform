from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CHAR, CheckConstraint, DateTime, Integer, LargeBinary, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class ConnectorModel(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        UniqueConstraint("name", name="uq_connectors_name"),
        CheckConstraint("timeout_seconds >= 1 AND timeout_seconds <= 60",
                        name="ck_connectors_timeout"),
        CheckConstraint(
            "(auth_type = 'NONE' AND ciphertext IS NULL AND tag IS NULL "
            "AND nonce IS NULL AND wrapped_dek IS NULL AND wrap_nonce IS NULL "
            "AND key_id IS NULL AND algorithm IS NULL) OR "
            "(auth_type <> 'NONE' AND ciphertext IS NOT NULL AND tag IS NOT NULL "
            "AND nonce IS NOT NULL AND wrapped_dek IS NOT NULL AND wrap_nonce IS NOT NULL "
            "AND key_id IS NOT NULL AND algorithm IS NOT NULL)",
            name="ck_connectors_credential_material",
        ),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(String(8192), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)
    auth_header: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tag: Mapped[bytes | None] = mapped_column(LargeBinary(16), nullable=True)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wrap_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)