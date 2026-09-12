import json
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4


CONTROL_SCHEMA_VERSION = 1
HEARTBEAT_MAX_AGE_SECONDS = 5.0
RESTART_DELAY_SECONDS = 2
MAX_CONTROL_FILE_BYTES = 4096


class ServiceTarget(StrEnum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    ALL = "all"


class ServiceControlError(RuntimeError):
    pass


class ServiceControlForbidden(ServiceControlError):
    pass


class ServiceControlUnavailable(ServiceControlError):
    pass


@dataclass(frozen=True, slots=True)
class ServiceControlStatus:
    enabled: bool
    supervisor_online: bool
    auto_restart_enabled: bool


@dataclass(frozen=True, slots=True)
class RestartRequest:
    schema_version: int
    target: ServiceTarget
    request_id: UUID
    requested_at: datetime
    not_before: datetime


class ServiceControlService:
    def __init__(
        self,
        enabled: bool,
        control_root: str | None,
        *,
        auto_restart_enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._enabled = enabled
        self._root = Path(control_root).resolve() if control_root else None
        self._auto_restart_enabled = auto_restart_enabled
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()

    def status(self) -> ServiceControlStatus:
        with self._lock:
            return ServiceControlStatus(
                enabled=self._enabled,
                supervisor_online=self._enabled and self._supervisor_online_unlocked(),
                auto_restart_enabled=self._auto_restart_enabled,
            )

    def request_restart(self, target: ServiceTarget | str) -> RestartRequest:
        try:
            resolved_target = ServiceTarget(target)
        except (TypeError, ValueError):
            raise ValueError("target must be frontend, backend, or all") from None
        with self._lock:
            if not self._enabled or not self._supervisor_online_unlocked():
                raise ServiceControlUnavailable("service supervisor is unavailable")
            now = self._aware_utc(self._clock())
            request = RestartRequest(
                schema_version=CONTROL_SCHEMA_VERSION,
                target=resolved_target,
                request_id=uuid4(),
                requested_at=now,
                not_before=now + timedelta(seconds=RESTART_DELAY_SECONDS),
            )
            self._write_request_unlocked(request)
            return request

    def _supervisor_online_unlocked(self) -> bool:
        if self._root is None:
            return False
        payload = self._read_json_object(self._root / "heartbeat.json")
        if payload is None or set(payload) != {"schema_version", "heartbeat_at"}:
            return False
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version != CONTROL_SCHEMA_VERSION:
            return False
        heartbeat_at = self._parse_timestamp(payload.get("heartbeat_at"))
        if heartbeat_at is None:
            return False
        age = (self._aware_utc(self._clock()) - heartbeat_at).total_seconds()
        return 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS

    def _write_request_unlocked(self, request: RestartRequest) -> None:
        if self._root is None or not self._root.is_dir():
            raise ServiceControlUnavailable("service control directory is unavailable")
        payload = {
            "schema_version": request.schema_version,
            "target": request.target.value,
            "request_id": str(request.request_id),
            "requested_at": request.requested_at.isoformat(),
            "not_before": request.not_before.isoformat(),
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_CONTROL_FILE_BYTES:
            raise ServiceControlUnavailable("restart request exceeds size limit")
        temporary = self._root / f".restart-request.{request.request_id}.tmp"
        destination = self._root / "restart-request.json"
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ServiceControlUnavailable("unable to write restart request") from exc

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, object] | None:
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONTROL_FILE_BYTES:
                return None
            with path.open("rb") as stream:
                raw = stream.read(MAX_CONTROL_FILE_BYTES + 1)
            if len(raw) > MAX_CONTROL_FILE_BYTES:
                return None
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)