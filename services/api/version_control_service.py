import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


GIT_TIMEOUT_SECONDS = 30.0
MAX_CHANGED_PATHS = 100
SENSITIVE_SUFFIXES = {
    ".bak", ".backup", ".bkp", ".db", ".db-journal", ".db-shm", ".db-wal",
    ".db3", ".dump", ".key", ".mdb", ".old", ".orig", ".p12", ".pem",
    ".pfx", ".save", ".sql", ".sql.gz", ".sqlite", ".sqlite-shm",
    ".sqlite-wal", ".sqlite3", ".sqlite3-shm", ".sqlite3-wal",
}
SENSITIVE_KEY_NAMES = {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}


def _normalize_windows_proxy(raw_value: str) -> str | None:
    raw = raw_value.strip()
    if not raw or any(character.isspace() for character in raw):
        return None
    if "=" in raw:
        entries = {}
        for part in raw.split(";"):
            key, separator, value = part.partition("=")
            if separator and key.strip() and value.strip():
                entries[key.strip().lower()] = value.strip()
        raw = entries.get("https") or entries.get("http") or ""
    if not raw or "@" in raw:
        return None
    candidate = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urlsplit(candidate)
        parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return candidate


def _windows_system_proxy() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            raw = winreg.QueryValueEx(key, "ProxyServer")[0]
    except (ImportError, OSError):
        return None
    return _normalize_windows_proxy(raw) if enabled == 1 and isinstance(raw, str) else None


class VersionControlError(RuntimeError):
    pass


class VersionControlDisabled(VersionControlError):
    pass


class VersionControlForbidden(VersionControlError):
    pass


class RepositoryUnavailable(VersionControlError):
    pass


class RemoteUnavailable(VersionControlError):
    pass


class DirtyWorkingTree(VersionControlError):
    pass


class DetachedHead(VersionControlError):
    pass


class SensitiveFilesStaged(VersionControlError):
    pass


class GitOperationFailed(VersionControlError):
    pass


class GitOperationTimedOut(VersionControlError):
    pass


@dataclass(frozen=True, slots=True)
class VersionControlStatus:
    enabled: bool
    repository_present: bool
    branch: str | None
    head_short: str | None
    clean: bool | None
    change_count: int
    changed_paths: tuple[str, ...]
    remote_configured: bool


class VersionControlService:
    def __init__(
        self, enabled: bool, repository_root: str | None,
        *, timeout_seconds: float = GIT_TIMEOUT_SECONDS,
    ) -> None:
        self._enabled = enabled
        self._root = Path(repository_root).resolve() if repository_root else None
        self._timeout_seconds = timeout_seconds
        self._lock = threading.RLock()

    def status(self) -> VersionControlStatus:
        with self._lock:
            return self._status_unlocked()

    def pull(self) -> VersionControlStatus:
        with self._lock:
            self._require_enabled_repository()
            if not self._origin_configured():
                raise RemoteUnavailable("origin is not configured")
            current = self._status_unlocked()
            if not current.clean:
                raise DirtyWorkingTree("working tree must be clean")
            if current.branch is None:
                raise DetachedHead("repository has no current branch")
            self._run("pull", "--ff-only", "origin", current.branch)
            return self._status_unlocked()

    def publish(self, commit_message: str) -> VersionControlStatus:
        with self._lock:
            self._require_enabled_repository()
            if not self._origin_configured():
                raise RemoteUnavailable("origin is not configured")
            self._run("add", "--all", "--")
            if any(self._is_sensitive(path) for path in self._staged_paths()):
                self._unstage_all()
                raise SensitiveFilesStaged("sensitive files cannot be published")
            staged = self._run("diff", "--cached", "--quiet", "--exit-code", check=False)
            if staged.returncode not in {0, 1}:
                raise GitOperationFailed("unable to inspect staged changes")
            if staged.returncode == 1:
                self._run("commit", "--no-gpg-sign", "-m", commit_message)
            self._run("push", "-u", "origin", "HEAD")
            return self._status_unlocked()

    def _status_unlocked(self) -> VersionControlStatus:
        if not self._enabled:
            return VersionControlStatus(False, False, None, None, None, 0, (), False)
        if not self._is_repository():
            return VersionControlStatus(True, False, None, None, None, 0, (), False)
        changed_paths = self._changed_paths()
        branch_result = self._run(
            "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        head_result = self._run("rev-parse", "--short=12", "HEAD", check=False)
        branch = self._text(branch_result.stdout) if branch_result.returncode == 0 else None
        head = self._text(head_result.stdout) if head_result.returncode == 0 else None
        return VersionControlStatus(
            True, True, branch or None, head or None, not changed_paths,
            len(changed_paths), tuple(changed_paths[:MAX_CHANGED_PATHS]),
            self._origin_configured(),
        )

    def _require_enabled_repository(self) -> None:
        if not self._enabled:
            raise VersionControlDisabled("version control is disabled")
        if not self._is_repository():
            raise RepositoryUnavailable("configured directory is not a repository")

    def _is_repository(self) -> bool:
        if self._root is None or not self._root.is_dir():
            return False
        try:
            inside = self._run("rev-parse", "--is-inside-work-tree", check=False)
            top = self._run("rev-parse", "--show-toplevel", check=False)
        except VersionControlError:
            return False
        if inside.returncode != 0 or self._text(inside.stdout) != "true":
            return False
        if top.returncode != 0:
            return False
        try:
            return Path(self._text(top.stdout)).resolve() == self._root
        except (OSError, ValueError):
            return False

    def _origin_configured(self) -> bool:
        remotes = self._run("remote")
        return "origin" in self._text(remotes.stdout).splitlines()

    def _changed_paths(self) -> list[str]:
        result = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = self._text(result.stdout, strip=False).split("\0")
        paths: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if len(entry) < 4:
                continue
            paths.append(entry[3:])
            if entry[0] in "RC" or entry[1] in "RC":
                index += 1
        return paths

    def _staged_paths(self) -> list[str]:
        result = self._run("diff", "--cached", "--name-only", "-z", "--")
        return [path for path in self._text(result.stdout, strip=False).split("\0") if path]

    def _unstage_all(self) -> None:
        head = self._run("rev-parse", "--verify", "HEAD", check=False)
        if head.returncode == 0:
            self._run("reset", "--mixed", "HEAD")
        else:
            self._run("rm", "--cached", "-r", "--quiet", "--", ".", check=False)

    @staticmethod
    def _is_sensitive(path: str) -> bool:
        normalized = path.replace("\\", "/")
        name = PurePosixPath(normalized).name.lower()
        if name != ".env.example" and (name == ".env" or name.startswith(".env.")):
            return True
        if name in SENSITIVE_KEY_NAMES:
            return True
        return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)

    def _run(
        self, *arguments: str, check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if self._root is None:
            raise RepositoryUnavailable("repository directory is not configured")
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GCM_INTERACTIVE"] = "Never"
        proxy = _windows_system_proxy()
        if proxy is not None:
            environment["HTTP_PROXY"] = proxy
            environment["HTTPS_PROXY"] = proxy
        try:
            result = subprocess.run(
                ["git", *arguments], cwd=str(self._root), env=environment,
                capture_output=True, timeout=self._timeout_seconds,
                check=False, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOperationTimedOut("git operation timed out") from exc
        except OSError as exc:
            raise GitOperationFailed("git executable is unavailable") from exc
        if check and result.returncode != 0:
            raise GitOperationFailed("git operation failed")
        return result

    @staticmethod
    def _text(value: bytes, *, strip: bool = True) -> str:
        decoded = value.decode("utf-8", errors="replace")
        return decoded.strip() if strip else decoded