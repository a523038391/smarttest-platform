"""Safe artifact collection and local/presigned object-store adapters."""
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from packages.protocol import EventEnvelope, EventType, TaskEnvelope
from services.api.domain import ArtifactRecord
from services.api.repository import RunRepository


class ArtifactUploadError(RuntimeError):
    pass


class ArtifactStore(Protocol):
    def put(self, source: Path, key: str, content_type: str) -> int: ...
    def local_path(self, key: str) -> Path | None: ...
    def download_url(self, key: str) -> str | None: ...


class LocalArtifactStore:
    def __init__(self, root: Path | str, maximum_bytes: int = 100 * 1024 * 1024) -> None:
        self.root = Path(root).resolve()
        self.maximum_bytes = maximum_bytes

    def put(self, source: Path, key: str, content_type: str) -> int:
        del content_type
        size = source.stat().st_size
        if size > self.maximum_bytes:
            raise ArtifactUploadError("artifact exceeds the configured size limit")
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if temporary.stat().st_size != size:
                raise ArtifactUploadError("artifact changed while it was uploaded")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return size

    def local_path(self, key: str) -> Path | None:
        target = self._target(key)
        if not target.is_file():
            return None
        return target

    def download_url(self, key: str) -> str | None:
        del key
        return None

    def _target(self, key: str) -> Path:
        parsed = PurePosixPath(key)
        if parsed.is_absolute() or ".." in parsed.parts or not parsed.name:
            raise ArtifactUploadError("artifact storage key is invalid")
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest


class PresignedArtifactStore:
    """Upload with short-lived URLs; object credentials stay server-side."""

    def __init__(
        self,
        put_url: Callable[[str, str], str],
        get_url: Callable[[str], str],
        allowed_hosts: set[str],
        session: requests.Session | None = None,
        timeout_seconds: float = 30,
        maximum_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.put_url = put_url
        self.get_url = get_url
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes

    def put(self, source: Path, key: str, content_type: str) -> int:
        url = self.put_url(key, content_type)
        self._validate_url(url)
        size = source.stat().st_size
        if size > self.maximum_bytes:
            raise ArtifactUploadError("artifact exceeds the configured size limit")
        try:
            with source.open("rb") as content:
                response = self.session.put(
                    url,
                    data=content,
                    headers={"Content-Type": content_type, "Content-Length": str(size)},
                    timeout=self.timeout_seconds, allow_redirects=False,
                )
            if not 200 <= response.status_code < 300:
                raise ArtifactUploadError("object storage rejected the artifact")
        except (OSError, requests.RequestException):
            raise ArtifactUploadError("artifact upload failed") from None
        return size

    def local_path(self, key: str) -> Path | None:
        del key
        return None

    def download_url(self, key: str) -> str | None:
        url = self.get_url(key)
        self._validate_url(url)
        return url

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ArtifactUploadError("object storage URL is not allowed")


@dataclass(frozen=True, slots=True)
class ArtifactCollector:
    repository: RunRepository
    store: ArtifactStore
    spool_root: Path

    def collect(self, task: TaskEnvelope, event: EventEnvelope) -> EventEnvelope:
        if event.type not in {EventType.ARTIFACT, EventType.SCREENSHOT}:
            return event
        relative_value = event.payload.get("relative_path")
        if not isinstance(relative_value, str) or len(relative_value) > 512:
            raise ArtifactUploadError("artifact relative_path is invalid")
        relative = PurePosixPath(relative_value)
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ArtifactUploadError("artifact relative_path is invalid")
        attempt_root = (self.spool_root / str(task.attempt_id)).resolve()
        source = attempt_root.joinpath(*relative.parts)
        if source.is_symlink():
            raise ArtifactUploadError("artifact symlinks are not allowed")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise ArtifactUploadError("artifact file does not exist") from exc
        if not resolved.is_relative_to(attempt_root) or not resolved.is_file():
            raise ArtifactUploadError("artifact path escapes its attempt spool")
        if len(resolved.name) > 255:
            raise ArtifactUploadError("artifact file name is too long")
        kind_value = event.payload.get("artifact_type", event.type.value)
        if not isinstance(kind_value, str) or not kind_value.isascii():
            raise ArtifactUploadError("artifact type is invalid")
        kind = kind_value.strip().lower()
        if not kind or len(kind) > 64 or not all(c.isalnum() or c in "_-" for c in kind):
            raise ArtifactUploadError("artifact type is invalid")
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        key = "/".join(
            str(value) for value in (
                task.tenant_id, task.project_id, task.run_id, task.attempt_id,
                event.event_id, resolved.name,
            )
        )
        size = self.store.put(resolved, key, content_type)
        artifact = self.repository.create_artifact(
            event.event_id, task.run_id, task.attempt_id, kind,
            resolved.name, content_type, size, key, event.occurred_at,
        )
        payload = {
            "artifact_id": str(artifact.id), "artifact_type": artifact.kind,
            "file_name": artifact.name, "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "url": f"/api/v1/runs/{task.run_id}/artifacts/{artifact.id}/content",
        }
        serialized = event.model_dump(mode="json")
        serialized["payload"] = payload
        return EventEnvelope.model_validate(serialized)


__all__ = [
    "ArtifactCollector", "ArtifactStore", "ArtifactUploadError", "LocalArtifactStore",
    "PresignedArtifactStore",
]