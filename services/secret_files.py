"""Ephemeral, file-only staging for secrets passed to Runner containers."""
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import os
from pathlib import Path
import re
from secrets import token_hex
import shutil
from typing import Protocol

from services.api.environment_repository import ResolvedSecretValue


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_COMMON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_CATEGORIES = {
    "environment_variable": _ENV_KEY,
    "common_parameter": _COMMON_KEY,
}
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


class SecretStagingError(RuntimeError):
    """Secret staging failed without disclosing sensitive context."""

    def __init__(self) -> None:
        super().__init__("secret staging failed")


class SecretRootVerifier(Protocol):
    def verify(self, root: Path) -> None: ...


class LinuxTmpfsVerifier:
    """Require an existing real directory on the deepest containing tmpfs mount."""

    def __init__(self, mountinfo_path: Path | str = "/proc/self/mountinfo") -> None:
        self.mountinfo_path = Path(mountinfo_path)

    def verify(self, root: Path) -> None:
        try:
            if root.is_symlink() or not root.exists() or not root.is_dir():
                raise SecretStagingError()
            resolved = root.resolve(strict=True)
            mounts = self._mounts()
            containing = [
                (mount, fstype) for mount, fstype in mounts
                if resolved == mount or resolved.is_relative_to(mount)
            ]
            if not containing:
                raise SecretStagingError()
            _, fstype = max(containing, key=lambda item: len(item[0].parts))
            if fstype != "tmpfs":
                raise SecretStagingError()
        except SecretStagingError:
            raise
        except Exception:
            raise SecretStagingError() from None

    def _mounts(self) -> list[tuple[Path, str]]:
        mounts: list[tuple[Path, str]] = []
        with self.mountinfo_path.open("r", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split()
                try:
                    separator = fields.index("-")
                    mount = Path(_MOUNT_ESCAPE.sub(
                        lambda match: chr(int(match.group(1), 8)), fields[4]
                    ))
                    fstype = fields[separator + 1]
                except (IndexError, ValueError):
                    raise SecretStagingError() from None
                mounts.append((mount, fstype))
        return mounts


def _change_owner(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)


def _restrict_mode(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)


class SecretFileStager:
    """Stage resolved values in one exclusively-created attempt directory."""

    def __init__(
        self,
        root: Path | str,
        *,
        verifier: SecretRootVerifier | Callable[[Path], None] | None = None,
        runner_uid: int = 65532,
        runner_gid: int = 65532,
        ownership_operation: Callable[[Path, int, int], None] = _change_owner,
        maximum_secret_bytes: int = 65_536,
    ) -> None:
        if runner_uid < 0 or runner_gid < 0 or maximum_secret_bytes <= 0:
            raise ValueError("secret stager configuration is invalid")
        self._root = Path(root)
        self._verifier = verifier or LinuxTmpfsVerifier()
        self._runner_uid = runner_uid
        self._runner_gid = runner_gid
        self._change_owner = ownership_operation
        self._maximum_secret_bytes = maximum_secret_bytes

    @contextmanager
    def stage(self, secrets: Sequence[ResolvedSecretValue]) -> Iterator[Path]:
        child: Path | None = None
        try:
            validated = self._validate(secrets)
            self._verify_root()
            child = self._create_child()
            self._write_values(child, validated)
        except Exception:
            if child is not None:
                self._cleanup(child)
            raise SecretStagingError() from None
        try:
            yield child
        finally:
            self._cleanup(child)

    def _verify_root(self) -> None:
        verify = getattr(self._verifier, "verify", None)
        if verify is not None:
            verify(self._root)
        else:
            self._verifier(self._root)  # type: ignore[operator]

    def _validate(
        self, secrets: Sequence[ResolvedSecretValue]
    ) -> tuple[tuple[str, str, bytes], ...]:
        validated: list[tuple[str, str, bytes]] = []
        identities: set[tuple[str, str]] = set()
        for secret in secrets:
            category = getattr(secret.category, "value", secret.category)
            pattern = _CATEGORIES.get(category)
            if pattern is None or not pattern.fullmatch(secret.name):
                raise SecretStagingError()
            identity = (category, secret.name)
            if identity in identities or not isinstance(secret.value, str):
                raise SecretStagingError()
            encoded = secret.value.encode("utf-8")
            if len(encoded) > self._maximum_secret_bytes:
                raise SecretStagingError()
            identities.add(identity)
            validated.append((category, secret.name, encoded))
        return tuple(validated)

    def _create_child(self) -> Path:
        for _ in range(128):
            child = self._root / token_hex(16)
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                continue
            try:
                _restrict_mode(child, 0o700)
                self._change_owner(child, self._runner_uid, self._runner_gid)
                return child
            except Exception:
                self._cleanup(child)
                raise
        raise SecretStagingError()

    def _write_values(
        self, child: Path, values: tuple[tuple[str, str, bytes], ...]
    ) -> None:
        categories: dict[str, Path] = {}
        for category, name, value in values:
            directory = categories.get(category)
            if directory is None:
                directory = child / category
                directory.mkdir(mode=0o700)
                _restrict_mode(directory, 0o700)
                self._change_owner(directory, self._runner_uid, self._runner_gid)
                categories[category] = directory
            path = directory / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                view = memoryview(value)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("secret write failed")
                    view = view[written:]
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:
                    _restrict_mode(path, 0o600)
            finally:
                os.close(descriptor)
            self._change_owner(path, self._runner_uid, self._runner_gid)

    @staticmethod
    def _cleanup(child: Path) -> None:
        try:
            shutil.rmtree(child)
        except Exception:
            raise SecretStagingError() from None


__all__ = [
    "LinuxTmpfsVerifier", "SecretFileStager", "SecretRootVerifier",
    "SecretStagingError",
]