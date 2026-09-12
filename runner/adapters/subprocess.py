"""Safe subprocess adapters for pytest and Playwright scripts."""

import json
import os
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Mapping

from packages.protocol import ResultOutcome, TaskEnvelope

from .base import (
    AdapterResult,
    deadline_remaining,
    execution_timeout,
    resolve_entrypoint,
    truncate,
)

_SAFE_ENVIRONMENT_NAMES = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "OS",
    "PATH",
    "PATHEXT",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
)
_RUNNER_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class SubprocessAdapter:
    engine_name = "subprocess"

    def __init__(
        self,
        output_limit: int = 16_384,
        *,
        python_executable: Path | str | None = None,
        pythonpath_entries: tuple[Path | str, ...] = (),
        artifact_root: Path | str | None = None,
    ) -> None:
        if output_limit <= 0:
            raise ValueError("output_limit must be positive")
        self.output_limit = output_limit
        self.python_executable = str(python_executable or sys.executable)
        self.pythonpath_entries = tuple(Path(item) for item in pythonpath_entries)
        self.artifact_root = Path(artifact_root) if artifact_root is not None else None

    def command(self, entrypoint: Path) -> list[str]:
        raise NotImplementedError

    def environment(
        self, task: TaskEnvelope, workspace: Path, parameters_file: Path
    ) -> Mapping[str, str]:
        source = {key.upper(): value for key, value in os.environ.items()}
        environment = {
            name: source[name] for name in _SAFE_ENVIRONMENT_NAMES if name in source
        }
        environment.update(task.environment_variables)
        environment["PYTHONPATH"] = os.pathsep.join([
            str(_RUNNER_PACKAGE_ROOT),
            str(workspace.resolve()),
            *(str(item.resolve()) for item in self.pythonpath_entries),
        ])
        environment["RUNNER_ENGINE"] = self.engine_name
        environment["RUNNER_WORKSPACE"] = str(workspace.resolve())
        environment["SMARTTEST_PARAMETERS_FILE"] = str(parameters_file)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        if self.artifact_root is not None:
            environment["SMARTTEST_ARTIFACTS_DIR"] = str(self.artifact_root.resolve())
        return environment

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
            taskkill = Path(system_root, "System32", "taskkill.exe") if system_root else None
            if taskkill is not None and taskkill.is_file():
                try:
                    subprocess.run(
                        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=5, check=False, shell=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _run_command(
        self, command: list[str], workspace: Path, environment: Mapping[str, str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        process_options: dict[str, object] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=str(workspace.resolve()),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **process_options,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._terminate_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                exc.cmd, exc.timeout, output=stdout or exc.output, stderr=stderr or exc.stderr
            ) from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def run(self, task: TaskEnvelope, workspace: Path) -> AdapterResult:
        try:
            deadline_remaining(task)
        except TimeoutError:
            return AdapterResult(
                ResultOutcome.TIMED_OUT,
                "Task deadline has passed",
                {"timed_out": True},
                timed_out=True,
            )
        try:
            entrypoint = resolve_entrypoint(workspace, task.entrypoint)
            timeout = execution_timeout(task)
        except TimeoutError:
            return AdapterResult(
                ResultOutcome.TIMED_OUT,
                "Task deadline has passed",
                {"timed_out": True},
                timed_out=True,
            )
        except (OSError, TypeError, ValueError):
            return AdapterResult(ResultOutcome.FAILED, "Invalid subprocess parameters")

        try:
            with TemporaryDirectory(prefix="smarttest-parameters-") as temporary:
                parameters_file = Path(temporary) / "parameters.json"
                descriptor = os.open(
                    parameters_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        task.model_dump(mode="json")["parameters"],
                        stream, ensure_ascii=False, allow_nan=False,
                        separators=(",", ":"),
                    )
                completed = self._run_command(
                    self.command(entrypoint), workspace,
                    self.environment(task, workspace, parameters_file), timeout,
                )
        except subprocess.TimeoutExpired as exc:
            return AdapterResult(
                ResultOutcome.TIMED_OUT,
                "Subprocess timed out",
                {"timed_out": True},
                truncate(exc.stdout, self.output_limit),
                truncate(exc.stderr, self.output_limit),
                timed_out=True,
            )
        except OSError as exc:
            return AdapterResult(
                ResultOutcome.INFRA_ERROR,
                "Subprocess could not be started",
                {"error_type": type(exc).__name__},
            )

        outcome = ResultOutcome.SUCCEEDED if completed.returncode == 0 else ResultOutcome.FAILED
        message = "Subprocess succeeded" if completed.returncode == 0 else "Subprocess failed"
        return AdapterResult(
            outcome,
            message,
            {"exit_code": completed.returncode},
            truncate(completed.stdout, self.output_limit),
            truncate(completed.stderr, self.output_limit),
            completed.returncode,
        )


class PytestAdapter(SubprocessAdapter):
    engine_name = "pytest"

    def command(self, entrypoint: Path) -> list[str]:
        return [
            self.python_executable, "-m", "pytest", "-p", "no:cacheprovider",
            str(entrypoint),
        ]


class PlaywrightAdapter(SubprocessAdapter):
    engine_name = "playwright"

    def command(self, entrypoint: Path) -> list[str]:
        return [self.python_executable, str(entrypoint)]
