"""Project-scoped, content-addressed storage for automation source projects."""
from io import BytesIO
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
from threading import BoundedSemaphore, Thread
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import zipfile


MAX_SOURCE_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 2_000
GIT_IMPORT_TIMEOUT_SECONDS = 60
ALLOWED_GIT_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "gitee.com"})
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_SOURCE_REF_PATTERN = re.compile(
    r"local-source:(v[12]):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):sha256:([0-9a-f]{64})"
)
_HOST_SOURCE_REF_PATTERN = re.compile(
    r"host-source:(v1):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):sha256:([0-9a-f]{64})"
)


class SourceStoreError(ValueError):
    """A source payload or trusted source reference is invalid."""


@dataclass(frozen=True, slots=True)
class StoredSource:
    source_ref: str
    content_digest: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class HostSource:
    project_directory: Path
    python_executable: Path


class LocalSourceStore:
    def __init__(
        self,
        root: Path | str,
        maximum_bytes: int = MAX_SOURCE_BYTES,
        *,
        host_execution_enabled: bool = False,
        host_project_roots: tuple[Path | str, ...] = (),
    ) -> None:
        if maximum_bytes <= 0 or maximum_bytes > MAX_SOURCE_BYTES:
            raise ValueError(f"maximum_bytes must be between 1 and {MAX_SOURCE_BYTES}")
        self.root = Path(root).resolve()
        self.maximum_bytes = maximum_bytes
        self._git_slots = BoundedSemaphore(2)
        self.host_execution_enabled = host_execution_enabled
        if host_execution_enabled and not host_project_roots:
            raise ValueError("host project roots are required when host execution is enabled")
        try:
            self.host_project_roots = tuple(
                Path(item).resolve(strict=True) for item in host_project_roots
            ) if host_execution_enabled else ()
        except (OSError, RuntimeError, TypeError):
            raise ValueError("host project roots must be existing directories") from None
        if any(not root.is_dir() or root.is_symlink() for root in self.host_project_roots):
            raise ValueError("host project roots must be existing directories")

    def put(self, project_id: UUID, content: str) -> StoredSource:
        project = self._project(project_id)
        if not isinstance(content, str):
            raise SourceStoreError("source content must be a string")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError:
            raise SourceStoreError("source content must be valid UTF-8") from None
        if len(encoded) > self.maximum_bytes:
            raise SourceStoreError("source content exceeds the 1 MiB limit")
        return self._put_bytes(project, encoded, "v1")

    def put_archive(self, project_id: UUID, content: bytes) -> StoredSource:
        project = self._project(project_id)
        if not isinstance(content, bytes):
            raise SourceStoreError("project archive must be bytes")
        if not content or len(content) > MAX_ARCHIVE_BYTES:
            raise SourceStoreError("project ZIP must be between 1 byte and 10 MiB")
        self._archive_entries(content)
        return self._put_bytes(project, content, "v2")

    def import_git(
        self, project_id: UUID, repository_url: str, git_ref: str = "HEAD"
    ) -> StoredSource:
        project = self._project(project_id)
        if not self._git_slots.acquire(blocking=False):
            raise SourceStoreError("Git importer is busy; try again shortly")
        try:
            url = self._git_url(repository_url)
            reference = self._git_ref(git_ref)
            with TemporaryDirectory(prefix="smarttest-git-") as temporary:
                repository = Path(temporary) / "repository.git"
                self._run_git(["init", "--quiet", "--bare", str(repository)])
                command = ["--git-dir", str(repository)]
                self._run_git(command + ["remote", "add", "origin", url])
                self._run_git(command + [
                    "fetch", "--quiet", "--depth=1",
                    f"--filter=blob:limit={MAX_ARCHIVE_BYTES}", "origin", reference,
                ])
                content = self._run_git_archive(command + [
                    "archive", "--format=zip", "FETCH_HEAD",
                ])
            return self.put_archive(project, content)
        finally:
            self._git_slots.release()

    def register_host(
        self, project_id: UUID, project_directory: str, python_executable: str
    ) -> StoredSource:
        project = self._project(project_id)
        directory = self._host_directory(project_directory)
        interpreter = self._host_interpreter(python_executable, directory)
        content = json.dumps(
            {
                "project_directory": str(directory),
                "python_executable": str(interpreter),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        self._atomic_write(self._host_source_path(project, digest), content)
        return StoredSource(self._host_reference(project, digest), digest, len(content))

    def resolve_host_reference(
        self, project_id: UUID, source_ref: str, digest: str, entrypoint: str
    ) -> HostSource:
        project = self._project(project_id)
        referenced_project, referenced_digest = self._parse_host_reference(source_ref)
        if referenced_project != project or referenced_digest != digest:
            raise SourceStoreError("host source reference does not match the project or digest")
        metadata = self._verified_host_metadata(project, digest)
        directory = self._host_directory(metadata["project_directory"])
        interpreter = self._host_interpreter(metadata["python_executable"], directory)
        relative = self._entrypoint(entrypoint)
        candidate = directory.joinpath(*relative.parts)
        try:
            resolved_entrypoint = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SourceStoreError("entrypoint does not exist in the host project") from None
        if (
            candidate.is_symlink() or not resolved_entrypoint.is_file()
            or not resolved_entrypoint.is_relative_to(directory)
        ):
            raise SourceStoreError("entrypoint is invalid for the host project")
        return HostSource(directory, interpreter)

    def validate_reference(
        self, project_id: UUID, source_ref: str, digest: str, entrypoint: str
    ) -> None:
        if source_ref.startswith("host-source:"):
            self.resolve_host_reference(project_id, source_ref, digest, entrypoint)
            return
        project = self._project(project_id)
        version, referenced_project, referenced_digest = self._parse_reference(source_ref)
        if referenced_project != project or referenced_digest != digest:
            raise SourceStoreError("source reference does not match the project or digest")
        relative = self._entrypoint(entrypoint)
        limit = self.maximum_bytes if version == "v1" else MAX_ARCHIVE_BYTES
        content = self._verified_content(project, digest, limit)
        if version == "v2" and str(relative) not in self._archive_entries(content, False):
            raise SourceStoreError("entrypoint does not exist in the project archive")

    def materialize(
        self,
        project_id: UUID,
        source_ref: str,
        digest: str,
        entrypoint: str,
        workspace: Path | str,
    ) -> Path:
        project = self._project(project_id)
        version, referenced_project, referenced_digest = self._parse_reference(source_ref)
        if referenced_project != project:
            raise SourceStoreError("source reference belongs to a different project")
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise SourceStoreError("content digest is invalid")
        if digest != referenced_digest:
            raise SourceStoreError("source reference and digest do not match")
        relative = self._entrypoint(entrypoint)
        limit = self.maximum_bytes if version == "v1" else MAX_ARCHIVE_BYTES
        content = self._verified_content(project, digest, limit)

        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if version == "v2":
            files = self._extract_archive(content, root)
            if str(relative) not in files:
                raise SourceStoreError("entrypoint does not exist in the project archive")
            return root.joinpath(*relative.parts)

        destination = root.joinpath(*relative.parts)
        parent = destination.parent.resolve()
        if not parent.is_relative_to(root):
            raise SourceStoreError("entrypoint escapes the workspace")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise SourceStoreError("entrypoint symlinks are not allowed")
        target = parent / destination.name
        self._atomic_write(target, content)
        return target

    def _verified_content(self, project: UUID, digest: str, limit: int) -> bytes:
        source = self._source_path(project, digest)
        if source.is_symlink():
            raise SourceStoreError("stored source symlinks are not allowed")
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            raise SourceStoreError("stored source does not exist") from None
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise SourceStoreError("stored source path is invalid")
        try:
            with resolved.open("rb") as stream:
                content = stream.read(limit + 1)
        except OSError:
            raise SourceStoreError("stored source could not be read") from None
        if len(content) > limit:
            raise SourceStoreError("stored source exceeds the size limit")
        if hashlib.sha256(content).hexdigest() != digest:
            raise SourceStoreError("stored source digest does not match")
        return content

    @staticmethod
    def _entrypoint(value: str) -> PurePosixPath:
        if (
            not isinstance(value, str) or not value or value != value.strip()
            or "\\" in value or any(ord(character) < 32 for character in value)
        ):
            raise SourceStoreError("entrypoint must be a safe relative POSIX path")
        path = PurePosixPath(value)
        if (
            path.is_absolute() or ".." in path.parts or "." in path.parts
            or not path.name or str(path) != value or value.endswith("/")
            or any(":" in part for part in path.parts)
        ):
            raise SourceStoreError("entrypoint must be a safe relative POSIX path")
        return path

    @staticmethod
    def _project(value: UUID) -> UUID:
        if not isinstance(value, UUID):
            raise SourceStoreError("project_id must be a UUID")
        return value

    @staticmethod
    def _reference(project_id: UUID, digest: str, version: str) -> str:
        return f"local-source:{version}:{project_id}:sha256:{digest}"

    @staticmethod
    def _parse_reference(value: str) -> tuple[str, UUID, str]:
        if not isinstance(value, str):
            raise SourceStoreError("source reference is invalid")
        match = _SOURCE_REF_PATTERN.fullmatch(value)
        if match is None:
            raise SourceStoreError("source reference is invalid")
        project = UUID(match.group(2))
        if str(project) != match.group(2):
            raise SourceStoreError("source reference is invalid")
        return match.group(1), project, match.group(3)

    def _put_bytes(self, project: UUID, content: bytes, version: str) -> StoredSource:
        digest = hashlib.sha256(content).hexdigest()
        self._atomic_write(self._source_path(project, digest), content)
        return StoredSource(self._reference(project, digest, version), digest, len(content))

    @classmethod
    def _archive_entries(
        cls, content: bytes, verify_payload: bool = True
    ) -> dict[str, zipfile.ZipInfo]:
        entries: dict[str, zipfile.ZipInfo] = {}
        names: dict[str, tuple[str, bool]] = {}
        total = 0
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                for info in archive.infolist():
                    path = cls._archive_path(info.filename)
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if info.flag_bits & 1:
                        raise SourceStoreError("encrypted ZIP entries are not allowed")
                    if stat.S_ISLNK(mode) or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise SourceStoreError("ZIP links and special files are not allowed")
                    is_directory = info.is_dir()
                    key = str(path).casefold()
                    if key in names:
                        raise SourceStoreError("ZIP contains duplicate or case-conflicting paths")
                    names[key] = (str(path), is_directory)
                    if is_directory:
                        continue
                    if len(entries) >= MAX_ARCHIVE_FILES:
                        raise SourceStoreError("ZIP contains too many files")
                    total += info.file_size
                    if total > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise SourceStoreError("expanded ZIP exceeds the 50 MiB limit")
                    entries[str(path)] = info
                for name in entries:
                    parent = PurePosixPath(name).parent
                    while str(parent) != ".":
                        existing = names.get(str(parent).casefold())
                        if existing is not None and not existing[1]:
                            raise SourceStoreError("ZIP file path conflicts with a parent file")
                        parent = parent.parent
                if not entries:
                    raise SourceStoreError("project ZIP does not contain any files")
                if verify_payload:
                    actual = 0
                    for info in entries.values():
                        with archive.open(info) as stream:
                            while chunk := stream.read(64 * 1024):
                                actual += len(chunk)
                                if actual > MAX_ARCHIVE_EXPANDED_BYTES:
                                    raise SourceStoreError("expanded ZIP exceeds the 50 MiB limit")
        except SourceStoreError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
            raise SourceStoreError("project source must be a valid, unencrypted ZIP") from None
        return entries

    @classmethod
    def _extract_archive(cls, content: bytes, root: Path) -> set[str]:
        entries = cls._archive_entries(content, False)
        expanded = 0
        with zipfile.ZipFile(BytesIO(content)) as archive:
            for name, info in entries.items():
                relative = PurePosixPath(name)
                destination = root.joinpath(*relative.parts)
                cls._safe_archive_parent(root, destination.parent)
                if destination.exists() or destination.is_symlink():
                    raise SourceStoreError("project archive would overwrite a workspace file")
                with archive.open(info) as source:
                    expanded += cls._atomic_write_stream(
                        destination, source, MAX_ARCHIVE_EXPANDED_BYTES - expanded
                    )
        return set(entries)

    @staticmethod
    def _archive_path(value: str) -> PurePosixPath:
        if (
            not value or len(value) > 512 or "\\" in value or "\x00" in value
            or value.startswith("/") or any(ord(character) < 32 for character in value)
        ):
            raise SourceStoreError("ZIP contains an unsafe path")
        normalized = value[:-1] if value.endswith("/") else value
        path = PurePosixPath(normalized)
        if (
            not normalized or path.is_absolute() or str(path) != normalized
            or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
        ):
            raise SourceStoreError("ZIP contains an unsafe path")
        return path

    @staticmethod
    def _safe_archive_parent(root: Path, parent: Path) -> None:
        current = root
        for part in parent.relative_to(root).parts:
            current /= part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise SourceStoreError("ZIP path conflicts with the workspace")
            current.mkdir(exist_ok=True)

    @staticmethod
    def _atomic_write_stream(target: Path, source: object, remaining: int) -> int:
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        copied = 0
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(64 * 1024):  # type: ignore[attr-defined]
                    copied += len(chunk)
                    if copied > remaining:
                        raise SourceStoreError("expanded ZIP exceeds the 50 MiB limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return copied

    @staticmethod
    def _git_url(value: str) -> str:
        if (
            not isinstance(value, str) or value != value.strip() or len(value) > 2048
            or any(ord(character) < 32 for character in value)
        ):
            raise SourceStoreError("Git repository URL is invalid")
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            raise SourceStoreError("Git repository URL is invalid") from None
        if (
            parsed.scheme != "https" or host not in ALLOWED_GIT_HOSTS
            or parsed.username is not None or parsed.password is not None
            or port not in {None, 443} or not parsed.path.strip("/")
            or parsed.query or parsed.fragment
        ):
            raise SourceStoreError(
                "Git URL must be a public HTTPS repository on GitHub, GitLab, Bitbucket, or Gitee"
            )
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise SourceStoreError("Git repository host could not be resolved") from None
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise SourceStoreError("Git repository host must resolve only to public addresses")
        return value

    @staticmethod
    def _git_ref(value: str) -> str:
        if not isinstance(value, str) or not value:
            return "HEAD"
        if (
            value != value.strip() or len(value) > 255 or value.startswith(("-", "."))
            or value.endswith(("/", ".", ".lock")) or ".." in value or "@{" in value
            or "//" in value or any(character.isspace() or character in "~^:?*[\\" for character in value)
            or any(part in {"", ".", ".."} or part.endswith(".lock") for part in value.split("/"))
        ):
            raise SourceStoreError("Git branch, tag, or commit reference is invalid")
        return value

    @staticmethod
    def _run_git(arguments: list[str]) -> None:
        command, environment = LocalSourceStore._git_invocation(arguments)
        try:
            result = subprocess.run(
                command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=GIT_IMPORT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SourceStoreError("Git repository import failed or timed out") from None
        if result.returncode != 0:
            raise SourceStoreError("Git repository import failed; check its URL and reference")

    @staticmethod
    def _run_git_archive(arguments: list[str]) -> bytes:
        command, environment = LocalSourceStore._git_invocation(arguments)
        try:
            process = subprocess.Popen(
                command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise SourceStoreError("Git repository import failed") from None
        chunks: list[bytes] = []
        overflow = []

        def consume() -> None:
            assert process.stdout is not None
            try:
                while chunk := process.stdout.read(64 * 1024):
                    if sum(map(len, chunks)) + len(chunk) > MAX_ARCHIVE_BYTES:
                        overflow.append(True)
                        process.kill()
                        return
                    chunks.append(chunk)
            except OSError:
                return

        reader = Thread(target=consume, daemon=True)
        reader.start()
        reader.join(GIT_IMPORT_TIMEOUT_SECONDS)
        if reader.is_alive():
            process.kill()
            reader.join(5)
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise SourceStoreError("Git repository import timed out") from None
        finally:
            if process.stdout is not None:
                process.stdout.close()
        if reader.is_alive():
            raise SourceStoreError("Git repository import timed out")
        if overflow:
            raise SourceStoreError("Git repository archive exceeds the 10 MiB limit")
        if return_code != 0:
            raise SourceStoreError("Git repository could not be archived")
        return b"".join(chunks)

    @staticmethod
    def _git_invocation(arguments: list[str]) -> tuple[list[str], dict[str, str]]:
        environment = {
            name: value for name, value in os.environ.items() if name.upper() in {
                "PATH", "SYSTEMROOT", "TEMP", "TMP", "HTTP_PROXY", "HTTPS_PROXY",
                "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
            }
        }
        environment.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        command = [
            "git", "-c", "credential.helper=", "-c", f"core.hooksPath={os.devnull}",
            "-c", "http.followRedirects=false", *arguments,
        ]
        return command, environment

    def _source_path(self, project_id: UUID, digest: str) -> Path:
        return self.root / str(project_id) / digest[:2] / digest

    def _host_source_path(self, project_id: UUID, digest: str) -> Path:
        return self.root / str(project_id) / "host" / digest[:2] / f"{digest}.json"

    @staticmethod
    def _host_reference(project_id: UUID, digest: str) -> str:
        return f"host-source:v1:{project_id}:sha256:{digest}"

    @staticmethod
    def _parse_host_reference(value: str) -> tuple[UUID, str]:
        if not isinstance(value, str):
            raise SourceStoreError("host source reference is invalid")
        match = _HOST_SOURCE_REF_PATTERN.fullmatch(value)
        if match is None:
            raise SourceStoreError("host source reference is invalid")
        project = UUID(match.group(2))
        if str(project) != match.group(2):
            raise SourceStoreError("host source reference is invalid")
        return project, match.group(3)

    def _verified_host_metadata(self, project_id: UUID, digest: str) -> dict[str, str]:
        if not self.host_execution_enabled:
            raise SourceStoreError("host execution is disabled")
        source = self._host_source_path(project_id, digest)
        if source.is_symlink():
            raise SourceStoreError("host source metadata is invalid")
        try:
            with source.resolve(strict=True).open("rb") as stream:
                content = stream.read(4097)
        except OSError:
            raise SourceStoreError("host source metadata does not exist") from None
        if len(content) > 4096 or hashlib.sha256(content).hexdigest() != digest:
            raise SourceStoreError("host source metadata is invalid")
        try:
            metadata = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceStoreError("host source metadata is invalid") from None
        if (
            not isinstance(metadata, dict) or set(metadata) != {
                "project_directory", "python_executable"
            } or not all(isinstance(value, str) for value in metadata.values())
        ):
            raise SourceStoreError("host source metadata is invalid")
        return metadata

    def _host_directory(self, value: str) -> Path:
        if not self.host_execution_enabled:
            raise SourceStoreError("host execution is disabled")
        if (
            not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise SourceStoreError("host project directory is invalid")
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise SourceStoreError("host project directory is invalid")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SourceStoreError("host project directory is invalid") from None
        if (
            not resolved.is_dir()
            or not any(resolved.is_relative_to(root) for root in self.host_project_roots)
        ):
            raise SourceStoreError("host project directory is outside the allowed roots")
        return resolved

    @staticmethod
    def _host_interpreter(value: str, project_directory: Path) -> Path:
        if (
            not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise SourceStoreError("host Python executable is invalid")
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise SourceStoreError("host Python executable is invalid")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SourceStoreError("host Python executable is invalid") from None
        if not resolved.is_file() or not resolved.is_relative_to(project_directory):
            raise SourceStoreError("host Python executable must be inside the project directory")
        return resolved

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "ALLOWED_GIT_HOSTS", "HostSource", "LocalSourceStore", "MAX_ARCHIVE_BYTES",
    "MAX_SOURCE_BYTES", "SourceStoreError", "StoredSource",
]