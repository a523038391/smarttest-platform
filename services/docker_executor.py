"""Docker CLI execution boundary for isolated Runner containers."""
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from threading import Thread
from typing import Protocol

from packages.protocol import EventEnvelope, ResultEnvelope, ResultOutcome, TaskEnvelope
from runner import EventSink
from services.secret_broker import SecretBroker, SecretCapability
from services.secret_files import SecretFileStager


class DockerExecutionError(RuntimeError):
    """The Docker runtime or its JSON-lines transport failed."""


@dataclass(frozen=True, slots=True)
class DockerContainerSpec:
    image: str
    name: str
    workspace: Path
    artifact_spool: Path | None = None
    labels: tuple[tuple[str, str], ...] = ()
    network: str = "none"
    cpus: str = "1.0"
    memory: str = "512m"
    pids_limit: int = 128
    secret_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class DockerRunResult:
    exit_code: int
    timed_out: bool = False
    stderr: str = ""


class DockerClient(Protocol):
    def run(
        self,
        spec: DockerContainerSpec,
        stdin_payload: str,
        on_stdout_line: Callable[[str], None],
        timeout_seconds: float,
    ) -> DockerRunResult: ...


class SubprocessDockerClient:
    """Invoke Docker without a daemon SDK; task data travels only over stdin."""

    _SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
    _SAFE_NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
    _SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    _SAFE_CPUS = re.compile(r"^[0-9]+(?:\.[0-9]{1,3})?$")
    _SAFE_MEMORY = re.compile(r"^[1-9][0-9]*(?:[bkmgBKMG])?$")

    def __init__(self, executable: str = "docker", stderr_limit: int = 65_536) -> None:
        self.executable = executable
        self.stderr_limit = stderr_limit

    def run(
        self,
        spec: DockerContainerSpec,
        stdin_payload: str,
        on_stdout_line: Callable[[str], None],
        timeout_seconds: float,
    ) -> DockerRunResult:
        command = self._command(spec)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise DockerExecutionError("Docker CLI could not be started") from exc

        stdout_errors: list[Exception] = []
        stderr_parts: list[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    on_stdout_line(line.rstrip("\r\n"))
                except Exception as exc:
                    stdout_errors.append(exc)
                    break

        def read_stderr() -> None:
            assert process.stderr is not None
            remaining = self.stderr_limit
            for chunk in iter(lambda: process.stderr.read(4096), ""):
                if remaining > 0:
                    saved = chunk[:remaining]
                    stderr_parts.append(saved)
                    remaining -= len(saved)

        readers = [Thread(target=read_stdout, daemon=True), Thread(target=read_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            assert process.stdin is not None
            try:
                process.stdin.write(stdin_payload)
                process.stdin.close()
            except OSError as exc:
                process.kill()
                raise DockerExecutionError("Runner rejected its task envelope") from exc
            try:
                exit_code = process.wait(timeout=max(0.01, timeout_seconds))
            except subprocess.TimeoutExpired:
                timed_out = True
                self._stop(spec.name)
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    exit_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    exit_code = -1
        finally:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdout, process.stderr):
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
            for reader in readers:
                reader.join(timeout=5)
            self._remove(spec.name)
        if any(reader.is_alive() for reader in readers):
            raise DockerExecutionError("Docker output streams did not close")
        if stdout_errors:
            raise DockerExecutionError("Runner emitted an invalid transport message") from stdout_errors[0]
        return DockerRunResult(exit_code, timed_out, "".join(stderr_parts))

    def _command(self, spec: DockerContainerSpec) -> list[str]:
        workspace, spool, secrets = self.validate_spec(spec)
        command = [
            self.executable, "run", "--rm", "--interactive", "--name", spec.name,
            "--read-only", "--user", "65532:65532", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", str(spec.pids_limit),
            "--cpus", spec.cpus, "--memory", spec.memory, "--memory-swap", spec.memory,
            "--network", spec.network, "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
            "--mount", f"type=bind,src={workspace},dst=/workspace,readonly",
        ]
        for key, value in spec.labels:
            command.extend(["--label", f"{key}={value}"])
        if spool is not None:
            command.extend(["--mount", f"type=bind,src={spool},dst=/artifacts"])
        if secrets is not None:
            command.extend([
                "--mount", f"type=bind,src={secrets},dst=/run/secrets,readonly"
            ])
        command.extend([spec.image, "python", "-m", "runner.container_entrypoint"])
        return command

    @classmethod
    def validate_spec(
        cls, spec: DockerContainerSpec
    ) -> tuple[Path, Path | None, Path | None]:
        if not isinstance(spec.image, str) or not cls._SAFE_IMAGE.fullmatch(spec.image):
            raise ValueError("runner image has an invalid reference")
        if not isinstance(spec.network, str) or not cls._SAFE_NETWORK.fullmatch(spec.network):
            raise ValueError("runner network has an invalid name")
        if not isinstance(spec.name, str) or not cls._SAFE_NAME.fullmatch(spec.name):
            raise ValueError("runner container has an invalid name")
        if (
            not isinstance(spec.cpus, str) or not cls._SAFE_CPUS.fullmatch(spec.cpus)
            or float(spec.cpus) <= 0 or not isinstance(spec.memory, str)
            or not cls._SAFE_MEMORY.fullmatch(spec.memory)
            or type(spec.pids_limit) is not int or spec.pids_limit <= 0
        ):
            raise ValueError("runner resource limits are invalid")
        for label in spec.labels:
            if not isinstance(label, tuple) or len(label) != 2:
                raise ValueError("runner label is invalid")
            key, value = label
            if not isinstance(key, str) or not re.fullmatch(
                r"[a-z][a-z0-9_.-]{0,62}", key
            ):
                raise ValueError("runner label has an invalid key")
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,255}", value
            ):
                raise ValueError("runner label has an invalid value")
        workspace = cls._directory(spec.workspace, "runner workspace")
        spool = (
            cls._directory(spec.artifact_spool, "artifact spool")
            if spec.artifact_spool is not None else None
        )
        secrets = None
        if spec.secret_directory is not None:
            try:
                secret_source = Path(spec.secret_directory)
            except TypeError:
                raise ValueError("runner secret directory is invalid") from None
            if secret_source.is_symlink():
                raise ValueError("runner secret directory is invalid")
            secrets = cls._directory(secret_source, "runner secret directory")
            if cls._overlaps(secrets, workspace) or (
                spool is not None and cls._overlaps(secrets, spool)
            ):
                raise ValueError("runner secret directory is invalid")
        return workspace, spool, secrets

    @staticmethod
    def _directory(path: Path | str, description: str) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except (OSError, RuntimeError, TypeError):
            raise ValueError(f"{description} must be a directory") from None
        if not resolved.is_dir() or any(character in str(resolved) for character in ",\r\n\0"):
            raise ValueError(f"{description} must be a directory")
        return resolved

    @staticmethod
    def _overlaps(left: Path, right: Path) -> bool:
        return (
            left == right or left.is_relative_to(right) or right.is_relative_to(left)
        )

    def _stop(self, name: str) -> None:
        self._control("stop", "--time", "2", name)

    def _remove(self, name: str) -> None:
        self._control("rm", "--force", name)

    def _control(self, *arguments: str) -> None:
        try:
            subprocess.run(
                [self.executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


class DockerExecutor:
    """Execute a task in a one-shot, resource-constrained Runner container."""

    def __init__(
        self,
        image: str,
        workspace: Path | str,
        client: DockerClient | None = None,
        network: str = "none",
        maximum_timeout_seconds: float = 330,
        artifact_spool_root: Path | str | None = None,
        secret_broker: SecretBroker | None = None,
        secret_file_stager: SecretFileStager | None = None,
        runner_id: str | None = None,
    ) -> None:
        self.image = image
        self.workspace = Path(workspace)
        self.client = client or SubprocessDockerClient()
        self.network = network
        self.maximum_timeout_seconds = maximum_timeout_seconds
        self.artifact_spool_root = (
            Path(artifact_spool_root) if artifact_spool_root is not None else None
        )
        self.secret_broker = secret_broker
        self.secret_file_stager = secret_file_stager
        self.runner_id = runner_id

    def execute(self, task: TaskEnvelope, event_sink: EventSink) -> ResultEnvelope:
        if task.secret_references and (
            self.secret_broker is None or self.secret_file_stager is None
            or not self.runner_id
        ):
            raise DockerExecutionError("Runner secret handling is not configured")
        remaining = (task.deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return self._terminal(task, ResultOutcome.TIMED_OUT, "deadline_expired")
        result: ResultEnvelope | None = None

        def consume(line: str) -> None:
            nonlocal result
            if not line or len(line) > 1_048_576:
                raise DockerExecutionError("Runner transport line is empty or too large")
            try:
                message = json.loads(line)
                kind = message["kind"]
                data = message["data"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise DockerExecutionError("Runner transport is not valid JSON-lines") from exc
            if kind == "event":
                event_sink(EventEnvelope.model_validate(data))
            elif kind == "result" and result is None:
                result = ResultEnvelope.model_validate(data)
            else:
                raise DockerExecutionError("Runner transport message kind is invalid or duplicated")

        artifact_spool = None
        if self.artifact_spool_root is not None:
            spool_root = self.artifact_spool_root.resolve()
            spool_root.mkdir(parents=True, exist_ok=True)
            artifact_spool = spool_root / str(task.attempt_id)
            artifact_spool.mkdir(exist_ok=False)
        try:
            if task.secret_references:
                runtime = self._run_with_secrets(
                    task, artifact_spool, consume, remaining
                )
            else:
                runtime = self._run_container(
                    task, artifact_spool, None, consume, remaining
                )
        finally:
            if artifact_spool is not None:
                shutil.rmtree(artifact_spool, ignore_errors=True)
        if runtime.timed_out:
            return self._terminal(task, ResultOutcome.TIMED_OUT, "container_timeout")
        if runtime.exit_code != 0 or result is None:
            return self._terminal(task, ResultOutcome.INFRA_ERROR, "container_failed")
        return result

    def _run_with_secrets(
        self,
        task: TaskEnvelope,
        artifact_spool: Path | None,
        consume: Callable[[str], None],
        remaining: float,
    ) -> DockerRunResult:
        assert self.secret_broker is not None
        assert self.secret_file_stager is not None
        assert self.runner_id is not None
        capability: SecretCapability | None = None
        stack = ExitStack()
        try:
            try:
                capability = self.secret_broker.issue(task, self.runner_id)
                values = self.secret_broker.redeem(capability, task, self.runner_id)
            except Exception:
                raise DockerExecutionError("Runner secret preparation failed") from None
            try:
                secret_directory = stack.enter_context(
                    self.secret_file_stager.stage(values)
                )
            except Exception:
                stack.close()
                raise DockerExecutionError("Runner secret preparation failed") from None
            try:
                return self._run_container(
                    task, artifact_spool, secret_directory, consume, remaining
                )
            finally:
                try:
                    stack.close()
                except Exception:
                    raise DockerExecutionError("Runner secret cleanup failed") from None
        finally:
            if capability is not None:
                try:
                    self.secret_broker.revoke(capability)
                except Exception:
                    pass

    def _run_container(
        self,
        task: TaskEnvelope,
        artifact_spool: Path | None,
        secret_directory: Path | None,
        consume: Callable[[str], None],
        remaining: float,
    ) -> DockerRunResult:
        spec = DockerContainerSpec(
            image=self.image,
            name=f"smarttest-{task.attempt_id}",
            workspace=self.workspace,
            artifact_spool=artifact_spool,
            secret_directory=secret_directory,
            labels=(
                ("smarttest.tenant_id", str(task.tenant_id)),
                ("smarttest.project_id", str(task.project_id)),
                ("smarttest.run_id", str(task.run_id)),
                ("smarttest.attempt_id", str(task.attempt_id)),
            ),
            network=self.network,
        )
        if secret_directory is not None:
            try:
                SubprocessDockerClient.validate_spec(spec)
            except ValueError:
                raise DockerExecutionError("Docker container configuration is invalid") from None
        return self.client.run(
            spec,
            task.model_dump_json() + "\n",
            consume,
            min(remaining, self.maximum_timeout_seconds),
        )

    @staticmethod
    def _terminal(
        task: TaskEnvelope, outcome: ResultOutcome, reason: str
    ) -> ResultEnvelope:
        return ResultEnvelope(
            run_id=task.run_id,
            attempt_id=task.attempt_id,
            engine=task.engine,
            outcome=outcome,
            duration_ms=0,
            summary={"reason": reason},
        )


__all__ = [
    "DockerClient", "DockerContainerSpec", "DockerExecutionError", "DockerExecutor",
    "DockerRunResult", "SubprocessDockerClient",
]