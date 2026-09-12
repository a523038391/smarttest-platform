"""Opt-in execution of trusted automation projects on the API host."""
from datetime import datetime, timezone
from pathlib import Path
import shutil

from packages.protocol import Engine, ResultEnvelope, TaskEnvelope
from runner import EventSink, PlaywrightAdapter, PytestAdapter, RunnerAgent


class HostExecutionError(RuntimeError):
    pass


class HostExecutor:
    """Run pytest or Playwright with a project-contained Python interpreter."""

    def __init__(
        self,
        workspace: Path | str,
        python_executable: Path | str,
        artifact_spool_root: Path | str,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.artifact_spool_root = Path(artifact_spool_root).resolve()

    def execute(self, task: TaskEnvelope, event_sink: EventSink) -> ResultEnvelope:
        if task.engine not in {Engine.PYTEST, Engine.PLAYWRIGHT}:
            raise HostExecutionError("host execution supports only pytest and Playwright")
        if task.secret_references:
            raise HostExecutionError("host execution does not support secret references")
        if task.deadline <= datetime.now(timezone.utc):
            raise HostExecutionError("host execution task deadline has expired")
        if (
            not self.workspace.is_dir() or not self.python_executable.is_file()
            or not self.python_executable.is_relative_to(self.workspace)
        ):
            raise HostExecutionError("host execution paths are no longer valid")

        self.artifact_spool_root.mkdir(parents=True, exist_ok=True)
        artifact_root = self.artifact_spool_root / str(task.attempt_id)
        artifact_root.mkdir(exist_ok=False)
        options = {
            "python_executable": self.python_executable,
            "pythonpath_entries": (self.workspace,),
            "artifact_root": artifact_root,
        }
        adapters = {
            Engine.PYTEST: PytestAdapter(**options),
            Engine.PLAYWRIGHT: PlaywrightAdapter(**options),
        }
        try:
            return RunnerAgent(
                task,
                self.workspace,
                event_sink,
                adapters=adapters,
                artifact_root=artifact_root,
            ).run()
        finally:
            shutil.rmtree(artifact_root, ignore_errors=True)


__all__ = ["HostExecutionError", "HostExecutor"]