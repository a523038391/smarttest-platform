import base64
import binascii
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_ATTEMPT_REAPER_INTERVAL_SECONDS = 30.0
DEFAULT_SECRET_CAPABILITY_TTL_SECONDS = 60
MAX_SECRET_CAPABILITY_TTL_SECONDS = 300
DEFAULT_SESSION_TTL_SECONDS = 86_400
MAX_SESSION_TTL_SECONDS = 31_536_000
DEFAULT_RUNNER_MAX_WORKERS = 2
MAX_RUNNER_WORKERS = 16
DEFAULT_RUNNER_POLL_INTERVAL_SECONDS = 0.5


def _boolean(environment: Mapping[str, str], name: str, default: bool) -> bool:
    value = environment.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None = field(default=None, repr=False)
    celery_broker_url: str = field(default=DEFAULT_REDIS_URL, repr=False)
    result_backend: str = field(default=DEFAULT_REDIS_URL, repr=False)
    celery_task_always_eager: bool = False
    auto_dispatch: bool = False
    artifact_root: str = ".artifacts"
    source_root: str = ".sources"
    local_runner_enabled: bool = False
    runner_image: str = "smarttest-runner:local"
    runner_network: str = "none"
    runner_work_root: str = ".runner-work"
    runner_max_workers: int = DEFAULT_RUNNER_MAX_WORKERS
    runner_poll_interval_seconds: float = DEFAULT_RUNNER_POLL_INTERVAL_SECONDS
    host_execution_enabled: bool = False
    host_project_roots: tuple[str, ...] = ()
    attempt_reaper_interval_seconds: float = DEFAULT_ATTEMPT_REAPER_INTERVAL_SECONDS
    secret_encryption_key: bytes | None = field(default=None, repr=False)
    secret_encryption_key_id: str | None = field(default=None, repr=False)
    secret_staging_root: str | None = None
    secret_capability_ttl_seconds: int = DEFAULT_SECRET_CAPABILITY_TTL_SECONDS
    runner_uid: int = 65532
    runner_gid: int = 65532
    auth_required: bool = True
    session_cookie_secure: bool = False
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    version_control_enabled: bool = False
    version_control_root: str | None = None
    service_control_enabled: bool = False
    service_control_root: str | None = None
    version_control_auto_restart: bool = False

    def __post_init__(self) -> None:
        configured_text = {
            "ARTIFACT_ROOT": self.artifact_root,
            "SOURCE_ROOT": self.source_root,
            "RUNNER_IMAGE": self.runner_image,
            "RUNNER_NETWORK": self.runner_network,
            "RUNNER_WORK_ROOT": self.runner_work_root,
        }
        for name, value in configured_text.items():
            if (
                not isinstance(value, str) or not value.strip()
                or value != value.strip() or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{name} must be a non-blank string without controls")
        if (
            isinstance(self.runner_max_workers, bool)
            or not isinstance(self.runner_max_workers, int)
            or not 1 <= self.runner_max_workers <= MAX_RUNNER_WORKERS
        ):
            raise ValueError(
                f"RUNNER_MAX_WORKERS must be between 1 and {MAX_RUNNER_WORKERS}"
            )
        if (
            not isinstance(self.runner_poll_interval_seconds, (int, float))
            or isinstance(self.runner_poll_interval_seconds, bool)
            or not math.isfinite(self.runner_poll_interval_seconds)
            or self.runner_poll_interval_seconds <= 0
        ):
            raise ValueError("RUNNER_POLL_INTERVAL_SECONDS must be positive")
        if self.host_execution_enabled and not self.host_project_roots:
            raise ValueError("HOST_PROJECT_ROOTS is required when host execution is enabled")
        for root in self.host_project_roots:
            if (
                not isinstance(root, str) or not root.strip() or root != root.strip()
                or any(ord(character) < 32 for character in root)
                or not Path(root).is_absolute()
            ):
                raise ValueError("HOST_PROJECT_ROOTS must contain absolute paths")
        if self.attempt_reaper_interval_seconds <= 0:
            raise ValueError("attempt_reaper_interval_seconds must be positive")
        if self.secret_encryption_key is not None and len(self.secret_encryption_key) != 32:
            raise ValueError("SECRET_ENCRYPTION_KEY must decode to exactly 32 bytes")
        if (self.secret_encryption_key is None) != (
            self.secret_encryption_key_id is None
        ):
            raise ValueError(
                "SECRET_ENCRYPTION_KEY and SECRET_ENCRYPTION_KEY_ID must both be set"
            )
        if (
            self.secret_encryption_key_id is not None
            and len(self.secret_encryption_key_id) > 255
        ):
            raise ValueError("SECRET_ENCRYPTION_KEY_ID cannot exceed 255 characters")
        if not 1 <= self.secret_capability_ttl_seconds <= MAX_SECRET_CAPABILITY_TTL_SECONDS:
            raise ValueError(
                f"SECRET_CAPABILITY_TTL_SECONDS must be between 1 and "
                f"{MAX_SECRET_CAPABILITY_TTL_SECONDS}"
            )
        if self.runner_uid < 0 or self.runner_gid < 0:
            raise ValueError("RUNNER_UID and RUNNER_GID cannot be negative")
        if not 1 <= self.session_ttl_seconds <= MAX_SESSION_TTL_SECONDS:
            raise ValueError(
                f"SESSION_TTL_SECONDS must be between 1 and {MAX_SESSION_TTL_SECONDS}"
            )
        if self.version_control_enabled and (
            not self.version_control_root
            or not isinstance(self.version_control_root, str)
            or self.version_control_root != self.version_control_root.strip()
            or any(ord(character) < 32 for character in self.version_control_root)
            or not Path(self.version_control_root).is_absolute()
        ):
            raise ValueError(
                "VERSION_CONTROL_ROOT must be an absolute directory when version control "
                "is enabled"
            )
        if self.service_control_enabled and (
            not self.service_control_root
            or not isinstance(self.service_control_root, str)
            or self.service_control_root != self.service_control_root.strip()
            or any(ord(character) < 32 for character in self.service_control_root)
            or not Path(self.service_control_root).is_absolute()
        ):
            raise ValueError(
                "SERVICE_CONTROL_ROOT must be an absolute directory when service control "
                "is enabled"
            )
        if self.version_control_auto_restart and not self.service_control_enabled:
            raise ValueError(
                "VERSION_CONTROL_AUTO_RESTART requires SERVICE_CONTROL_ENABLED=true"
            )

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environment is None else environment
        database_url = env.get("DATABASE_URL", "").strip() or None
        encoded_key = env.get("SECRET_ENCRYPTION_KEY", "").strip()
        try:
            encryption_key = base64.b64decode(encoded_key, validate=True) if encoded_key else None
        except (binascii.Error, ValueError):
            raise ValueError("SECRET_ENCRYPTION_KEY must be valid base64") from None
        return cls(
            database_url=database_url,
            celery_broker_url=env.get("CELERY_BROKER_URL", DEFAULT_REDIS_URL),
            result_backend=env.get(
                "RESULT_BACKEND",
                env.get("CELERY_RESULT_BACKEND", DEFAULT_REDIS_URL),
            ),
            celery_task_always_eager=_boolean(
                env, "CELERY_TASK_ALWAYS_EAGER", False
            ),
            auto_dispatch=_boolean(env, "AUTO_DISPATCH", False),
            artifact_root=env.get("ARTIFACT_ROOT", ".artifacts").strip() or ".artifacts",
            source_root=env.get("SOURCE_ROOT", ".sources").strip() or ".sources",
            local_runner_enabled=_boolean(env, "LOCAL_RUNNER_ENABLED", False),
            runner_image=env.get("RUNNER_IMAGE", "smarttest-runner:local").strip()
            or "smarttest-runner:local",
            runner_network=env.get("RUNNER_NETWORK", "none").strip() or "none",
            runner_work_root=env.get("RUNNER_WORK_ROOT", ".runner-work").strip()
            or ".runner-work",
            runner_max_workers=int(
                env.get("RUNNER_MAX_WORKERS", DEFAULT_RUNNER_MAX_WORKERS)
            ),
            runner_poll_interval_seconds=float(env.get(
                "RUNNER_POLL_INTERVAL_SECONDS", DEFAULT_RUNNER_POLL_INTERVAL_SECONDS
            )),
            host_execution_enabled=_boolean(env, "HOST_EXECUTION_ENABLED", False),
            host_project_roots=tuple(
                part.strip() for part in env.get("HOST_PROJECT_ROOTS", "").split(os.pathsep)
                if part.strip()
            ),
            attempt_reaper_interval_seconds=float(
                env.get(
                    "ATTEMPT_REAPER_INTERVAL_SECONDS",
                    DEFAULT_ATTEMPT_REAPER_INTERVAL_SECONDS,
                )
            ),
            secret_encryption_key=encryption_key,
            secret_encryption_key_id=(
                env.get("SECRET_ENCRYPTION_KEY_ID", "").strip() or None
            ),
            secret_staging_root=(
                env.get("SECRET_STAGING_ROOT", "").strip() or None
            ),
            secret_capability_ttl_seconds=int(
                env.get(
                    "SECRET_CAPABILITY_TTL_SECONDS",
                    DEFAULT_SECRET_CAPABILITY_TTL_SECONDS,
                )
            ),
            runner_uid=int(env.get("RUNNER_UID", "65532")),
            runner_gid=int(env.get("RUNNER_GID", "65532")),
            auth_required=_boolean(env, "AUTH_REQUIRED", True),
            session_cookie_secure=_boolean(env, "SESSION_COOKIE_SECURE", False),
            session_ttl_seconds=int(
                env.get("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS)
            ),
            version_control_enabled=_boolean(env, "VERSION_CONTROL_ENABLED", False),
            version_control_root=(
                env.get("VERSION_CONTROL_ROOT", "").strip() or None
            ),
            service_control_enabled=_boolean(env, "SERVICE_CONTROL_ENABLED", False),
            service_control_root=(
                env.get("SERVICE_CONTROL_ROOT", "").strip() or None
            ),
            version_control_auto_restart=_boolean(
                env, "VERSION_CONTROL_AUTO_RESTART", False
            ),
        )