"""Windows-only local supervisor for the API and Vite development server."""

import argparse
import json
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4


SCHEMA_VERSION = 1
MAX_CONTROL_FILE_BYTES = 4096
HEARTBEAT_INTERVAL_SECONDS = 1.0
LOOP_INTERVAL_SECONDS = 0.25
RESTART_DELAY_SECONDS = 2
TARGETS = {"frontend", "backend", "all"}


@dataclass(frozen=True, slots=True)
class ControlRequest:
    target: str
    request_id: UUID
    requested_at: datetime
    not_before: datetime


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


def _read_json(path: Path) -> dict[str, object] | None:
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


def parse_control_request(path: Path) -> ControlRequest | None:
    payload = _read_json(path)
    required = {"schema_version", "target", "request_id", "requested_at", "not_before"}
    if payload is None or set(payload) != required:
        return None
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        return None
    target = payload["target"]
    request_id_text = payload["request_id"]
    if not isinstance(target, str) or target not in TARGETS:
        return None
    if not isinstance(request_id_text, str):
        return None
    try:
        request_id = UUID(request_id_text)
    except ValueError:
        return None
    if str(request_id) != request_id_text:
        return None
    requested_at = _parse_timestamp(payload["requested_at"])
    not_before = _parse_timestamp(payload["not_before"])
    if requested_at is None or not_before is None:
        return None
    if not_before - requested_at != timedelta(seconds=RESTART_DELAY_SECONDS):
        return None
    return ControlRequest(target, request_id, requested_at, not_before)


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CONTROL_FILE_BYTES:
        raise RuntimeError("control payload exceeds size limit")
    temporary = path.parent / f".{path.name}.{uuid4()}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PlatformSupervisor:
    def __init__(self, repository_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._control_root = self._repository_root / ".service-control"
        self._request_path = self._control_root / "restart-request.json"
        self._processing_path = self._control_root / "restart-request.processing.json"
        self._heartbeat_path = self._control_root / "heartbeat.json"
        self._processes: dict[str, subprocess.Popen[bytes] | None] = {
            "frontend": None,
            "backend": None,
        }
        self._commands = {
            "backend": [
                str(self._repository_root / ".venv" / "Scripts" / "python.exe"),
                "-m", "uvicorn", "services.api.app:app", "--host", "127.0.0.1",
                "--port", "8000",
            ],
            "frontend": [
                "cmd.exe", "/d", "/s", "/c", "npm.cmd", "run", "dev", "--",
                "--host", "127.0.0.1",
            ],
        }
        self._working_directories = {
            "backend": self._repository_root,
            "frontend": self._repository_root / "apps" / "web",
        }
        self._child_environment = os.environ.copy()
        self._child_environment.update({
            "SERVICE_CONTROL_ENABLED": "true",
            "SERVICE_CONTROL_ROOT": str(self._control_root),
            "VERSION_CONTROL_AUTO_RESTART": "true",
        })

    def run(self) -> None:
        self._validate_layout()
        self._control_root.mkdir(exist_ok=True)
        pending: ControlRequest | None = None
        last_heartbeat = 0.0
        try:
            self._start("backend")
            self._start("frontend")
            while True:
                self._ensure_children_running()
                monotonic_now = time.monotonic()
                if monotonic_now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    self._write_heartbeat()
                    last_heartbeat = monotonic_now
                if pending is None and self._claim_request():
                    pending = parse_control_request(self._processing_path)
                    if pending is None:
                        self._processing_path.unlink(missing_ok=True)
                if pending is not None and datetime.now(timezone.utc) >= pending.not_before:
                    self._restart(pending.target)
                    self._processing_path.unlink(missing_ok=True)
                    pending = None
                time.sleep(LOOP_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("Stopping platform services...")
        finally:
            self._heartbeat_path.unlink(missing_ok=True)
            self._stop("frontend")
            self._stop("backend")

    def _validate_layout(self) -> None:
        python = Path(self._commands["backend"][0])
        if not python.is_file():
            raise RuntimeError(f"backend Python executable not found: {python}")
        if not self._working_directories["frontend"].is_dir():
            raise RuntimeError("apps/web directory not found")

    def _write_heartbeat(self) -> None:
        _atomic_write(self._heartbeat_path, {
            "schema_version": SCHEMA_VERSION,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        })

    def _claim_request(self) -> bool:
        if self._processing_path.exists():
            return True
        try:
            os.replace(self._request_path, self._processing_path)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return True

    def _start(self, target: str) -> None:
        self._processes[target] = subprocess.Popen(
            self._commands[target],
            cwd=str(self._working_directories[target]),
            env=self._child_environment,
            shell=False,
        )
        print(f"Started {target} service")

    def _stop(self, target: str) -> None:
        process = self._processes[target]
        if process is None or process.poll() is not None:
            self._processes[target] = None
            return
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=10,
                shell=False,
            )
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._processes[target] = None

    def _restart(self, target: str) -> None:
        targets = ("frontend", "backend") if target == "all" else (target,)
        for item in targets:
            self._stop(item)
        for item in targets:
            self._start(item)

    def _ensure_children_running(self) -> None:
        for target, process in tuple(self._processes.items()):
            if process is None or process.poll() is not None:
                self._start(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="fixed repository root used for all child process paths",
    )
    arguments = parser.parse_args()
    if os.name != "nt":
        parser.error("platform_supervisor.py supports Windows only")
    PlatformSupervisor(arguments.repository_root).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())