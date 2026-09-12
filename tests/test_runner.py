from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
import subprocess
import sys
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from packages.protocol import Engine, EventType, ResultOutcome, SecretReference, TaskEnvelope
from runner import (
    AdapterResult, HTTPAdapter, PlaywrightAdapter, PytestAdapter, RunnerAgent,
    load_parameters, secret_path,
)


def test_parameter_helper_import_does_not_require_runner_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-S", "-c", "from runner import load_parameters"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def task(
    engine: Engine = Engine.PYTEST,
    entrypoint: str = "test_case.py",
    parameters: dict | None = None,
    deadline: datetime | None = None,
) -> TaskEnvelope:
    now = datetime.now(timezone.utc)
    resolved_deadline = deadline or now + timedelta(minutes=5)
    created_at = min(now, resolved_deadline - timedelta(minutes=1))
    return TaskEnvelope(
        run_id=uuid4(),
        attempt_id=uuid4(),
        task_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        idempotency_key="runner-test",
        engine=engine,
        created_at=created_at,
        deadline=resolved_deadline,
        entrypoint=entrypoint,
        parameters=parameters or {},
    )


def test_secret_path_is_deterministic_and_rejects_invalid_names(tmp_path: Path) -> None:
    reference = SecretReference(
        category="environment_variable", name="TOKEN", secret_ref=uuid4()
    )
    assert secret_path(reference, tmp_path) == tmp_path.resolve() / "environment_variable" / "TOKEN"
    invalid = reference.model_copy(update={"name": "../escape"})
    with pytest.raises(ValueError, match="secret reference is invalid"):
        secret_path(invalid, tmp_path)


def test_agent_emits_monotonic_events_from_fake_adapter(tmp_path: Path) -> None:
    class FakeAdapter:
        def run(self, envelope: TaskEnvelope, workspace: Path) -> AdapterResult:
            assert workspace == tmp_path
            return AdapterResult(ResultOutcome.FAILED, "assertion failed", stdout="output")

    events = []
    envelope = task()
    result = RunnerAgent(envelope, tmp_path, events.append, {Engine.PYTEST: FakeAdapter()}).run()

    assert result.outcome is ResultOutcome.FAILED
    assert [event.seq for event in events] == list(range(len(events)))
    assert [event.type.value for event in events] == [
        "started", "log", "assertion", "error", "finished"
    ]
    assert all(event.run_id == envelope.run_id for event in events)


def test_event_sink_failure_does_not_advance_sequence(tmp_path: Path) -> None:
    sink = Mock(side_effect=[RuntimeError("unavailable"), None])
    agent = RunnerAgent(task(), tmp_path, sink)

    with pytest.raises(RuntimeError, match="unavailable"):
        agent._emit(EventType.HEARTBEAT, {})
    agent._emit(EventType.HEARTBEAT, {})

    assert [call.args[0].seq for call in sink.call_args_list] == [0, 0]


def test_http_adapter_uses_session_asserts_status_and_hides_secrets(tmp_path: Path) -> None:
    session = Mock()
    session.request.return_value.status_code = 201
    secret = "never-emit-this"
    envelope = task(
        Engine.HTTP,
        parameters={"request": {
            "method": "post",
            "url": f"https://example.invalid/items?secret={secret}",
            "headers": {"Authorization": secret, "Cookie": secret},
            "json": {"name": "case"},
            "timeout": 2,
            "expected_status": 201,
        }},
    )
    events = []
    result = RunnerAgent(
        envelope,
        tmp_path,
        events.append,
        {Engine.HTTP: HTTPAdapter(session, resolver=lambda _: ["93.184.216.34"])},
    ).run()

    assert result.outcome is ResultOutcome.SUCCEEDED
    session.request.assert_called_once()
    assert session.request.call_args.kwargs["method"] == "POST"
    assert session.request.call_args.kwargs["allow_redirects"] is False
    assert session.request.call_args.kwargs["timeout"] <= 2
    assert all(secret not in event.model_dump_json() for event in events)
    assert secret not in result.model_dump_json()

    session.request.return_value.status_code = 500
    adapter = HTTPAdapter(session, resolver=lambda _: ["93.184.216.34"])
    assert adapter.run(envelope, tmp_path).outcome is ResultOutcome.FAILED


@pytest.mark.parametrize(
    ("url", "resolved"),
    [
        ("http://10.0.0.1/path", ["93.184.216.34"]),
        ("http://localhost/path", ["127.0.0.1"]),
        ("ftp://example.test/path", ["93.184.216.34"]),
        ("https://user:password@example.test/path", ["93.184.216.34"]),
        ("https:///missing-host", ["93.184.216.34"]),
    ],
)
def test_http_adapter_rejects_unsafe_urls(
    tmp_path: Path, url: str, resolved: list[str]
) -> None:
    session = Mock()
    result = HTTPAdapter(session, resolver=lambda _: resolved).run(
        task(Engine.HTTP, parameters={"request": {"url": url}}), tmp_path
    )

    assert result.outcome is ResultOutcome.FAILED
    session.request.assert_not_called()


def test_http_adapter_does_not_request_after_deadline(tmp_path: Path) -> None:
    session = Mock()
    resolver = Mock(return_value=["93.184.216.34"])
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)

    result = HTTPAdapter(session, resolver=resolver).run(
        task(
            Engine.HTTP,
            parameters={"request": {"url": "https://example.test"}},
            deadline=expired,
        ),
        tmp_path,
    )

    assert result.outcome is ResultOutcome.TIMED_OUT
    assert result.timed_out is True
    session.request.assert_not_called()
    resolver.assert_not_called()


def test_subprocess_adapter_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("pass", encoding="utf-8")
    with patch("runner.adapters.subprocess.SubprocessAdapter._run_command") as run:
        result = PytestAdapter().run(task(entrypoint=str(outside)), tmp_path)

    assert result.outcome is ResultOutcome.FAILED
    run.assert_not_called()


def test_subprocess_does_not_start_after_deadline(tmp_path: Path) -> None:
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with patch("runner.adapters.subprocess.SubprocessAdapter._run_command") as run:
        result = PytestAdapter().run(task(entrypoint="missing.py", deadline=expired), tmp_path)

    assert result.outcome is ResultOutcome.TIMED_OUT
    assert result.timed_out is True
    run.assert_not_called()


@pytest.mark.parametrize(
    ("returncode", "outcome"),
    [(0, ResultOutcome.SUCCEEDED), (3, ResultOutcome.FAILED)],
)
def test_pytest_subprocess_success_and_failure(
    tmp_path: Path, returncode: int, outcome: ResultOutcome
) -> None:
    entrypoint = tmp_path / "test_case.py"
    entrypoint.write_text("pass", encoding="utf-8")
    completed = CompletedProcess([], returncode, stdout="x" * 40, stderr="problem")
    with patch(
        "runner.adapters.subprocess.SubprocessAdapter._run_command", return_value=completed
    ) as run:
        result = PytestAdapter(output_limit=20).run(task(parameters={"timeout": 10}), tmp_path)

    assert result.outcome is outcome
    assert result.stdout.endswith("...[truncated]")
    assert len(result.stdout) == 20
    command, workspace, environment, timeout = run.call_args.args
    assert command[:5] == [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
    assert workspace == tmp_path
    assert timeout <= 10
    assert environment["RUNNER_ENGINE"] == "pytest"
    assert environment["RUNNER_WORKSPACE"] == str(tmp_path.resolve())


def test_subprocess_receives_public_environment_and_data_parameters(tmp_path: Path) -> None:
    entrypoint = tmp_path / "test_case.py"
    entrypoint.write_text("pass", encoding="utf-8")
    observed = {}

    def execute(_command, _workspace, environment, _timeout):
        observed["environment"] = environment
        observed["path"] = Path(environment["SMARTTEST_PARAMETERS_FILE"])
        observed["parameters"] = load_parameters(
            environment["SMARTTEST_PARAMETERS_FILE"]
        )
        return CompletedProcess([], 0, stdout="", stderr="")

    envelope = task(parameters={"user": "alice", "iteration": 2}).model_copy(
        update={"environment_variables": {"BASE_URL": "https://example.test"}}
    )
    with patch(
        "runner.adapters.subprocess.SubprocessAdapter._run_command", side_effect=execute
    ):
        result = PytestAdapter().run(envelope, tmp_path)

    assert result.outcome is ResultOutcome.SUCCEEDED
    assert observed["environment"]["BASE_URL"] == "https://example.test"
    assert observed["environment"]["PYTHONPATH"]
    assert observed["parameters"] == {"user": "alice", "iteration": 2}
    assert not observed["path"].exists()


def test_subprocess_supports_host_interpreter_and_artifact_directory(
    tmp_path: Path,
) -> None:
    entrypoint = tmp_path / "test_case.py"
    entrypoint.write_text("pass", encoding="utf-8")
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_imports = tmp_path / "support"
    artifacts = tmp_path / "artifacts"
    interpreter.parent.mkdir(parents=True)
    project_imports.mkdir()
    interpreter.write_bytes(b"python")
    with patch(
        "runner.adapters.subprocess.SubprocessAdapter._run_command",
        return_value=CompletedProcess([], 0, "", ""),
    ) as run:
        result = PytestAdapter(
            python_executable=interpreter,
            pythonpath_entries=(project_imports,),
            artifact_root=artifacts,
        ).run(task(), tmp_path)

    command, _, environment, _ = run.call_args.args
    assert result.outcome is ResultOutcome.SUCCEEDED
    assert command[0] == str(interpreter)
    assert str(project_imports.resolve()) in environment["PYTHONPATH"].split(os.pathsep)
    assert environment["SMARTTEST_ARTIFACTS_DIR"] == str(artifacts.resolve())


@pytest.mark.parametrize("engine", [Engine.PYTEST, Engine.PLAYWRIGHT])
def test_subprocess_script_reads_data_parameters(
    tmp_path: Path, engine: Engine
) -> None:
    entrypoint = tmp_path / "data_case.py"
    if engine is Engine.PYTEST:
        entrypoint.write_text(
            "from runner import load_parameters\n"
            "def test_data():\n"
            "    assert load_parameters() == {'user': 'alice', 'iteration': 2}\n",
            encoding="utf-8",
        )
    else:
        entrypoint.write_text(
            "from runner import load_parameters\n"
            "assert load_parameters() == {'user': 'alice', 'iteration': 2}\n",
            encoding="utf-8",
        )

    result = (
        PytestAdapter() if engine is Engine.PYTEST else PlaywrightAdapter()
    ).run(
        task(
            engine, entrypoint="data_case.py",
            parameters={"user": "alice", "iteration": 2},
        ),
        tmp_path,
    )

    assert result.outcome is ResultOutcome.SUCCEEDED


def test_playwright_nested_entrypoint_imports_project_root_module(tmp_path: Path) -> None:
    package = tmp_path / "conf"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "baseconfig.py").write_text("VALUE = 'available'\n", encoding="utf-8")
    entrypoint = tmp_path / "lib" / "firstleg" / "script.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(
        "from conf.baseconfig import VALUE\nassert VALUE == 'available'\n",
        encoding="utf-8",
    )

    result = PlaywrightAdapter().run(
        task(Engine.PLAYWRIGHT, entrypoint="lib/firstleg/script.py"), tmp_path
    )

    assert result.outcome is ResultOutcome.SUCCEEDED


def test_subprocess_timeout_and_playwright_environment(tmp_path: Path) -> None:
    entrypoint = tmp_path / "test_case.py"
    entrypoint.write_text("pass", encoding="utf-8")
    expired = TimeoutExpired(["python"], 1, output="partial", stderr="late")
    process = Mock(pid=123, returncode=-9)
    process.communicate.side_effect = [expired, ("partial", "late")]
    process.poll.return_value = None
    with patch("runner.adapters.subprocess.subprocess.Popen", return_value=process) as popen:
        with patch.object(PytestAdapter, "_terminate_process_tree") as terminate:
            result = PytestAdapter().run(task(), tmp_path)
    assert result.outcome is ResultOutcome.TIMED_OUT
    assert result.timed_out is True
    assert result.stdout == "partial"
    terminate.assert_called_once_with(process)
    assert popen.call_args.kwargs["encoding"] == "utf-8"
    if os.name == "nt":
        assert popen.call_args.kwargs["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert popen.call_args.kwargs["start_new_session"] is True

    with patch(
        "runner.adapters.subprocess.SubprocessAdapter._run_command",
        return_value=CompletedProcess([], 0, "", ""),
    ) as run:
        PlaywrightAdapter().run(task(Engine.PLAYWRIGHT), tmp_path)
    command, _, environment, _ = run.call_args.args
    assert command == [sys.executable, str(entrypoint.resolve())]
    assert environment["RUNNER_ENGINE"] == "playwright"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree behavior")
def test_windows_subprocess_termination_uses_system_taskkill() -> None:
    process = Mock(pid=321)
    process.poll.return_value = 0

    with patch("runner.adapters.subprocess.subprocess.run") as run:
        PytestAdapter._terminate_process_tree(process)

    command = run.call_args.args[0]
    assert Path(command[0]).name.lower() == "taskkill.exe"
    assert command[1:] == ["/PID", "321", "/T", "/F"]
    process.kill.assert_not_called()


@pytest.mark.parametrize("adapter", [PytestAdapter(), PlaywrightAdapter()])
def test_subprocess_environment_does_not_inherit_secrets(
    tmp_path: Path, adapter: PytestAdapter | PlaywrightAdapter
) -> None:
    entrypoint = tmp_path / "test_case.py"
    entrypoint.write_text("pass", encoding="utf-8")
    secret = "database-and-token-secret"
    inherited = {
        "PATH": "safe-path",
        "SYSTEMROOT": "C:\\Windows",
        "PLAYWRIGHT_BROWSERS_PATH": "C:\\browsers",
        "DATABASE_URL": secret,
        "SERVICE_TOKEN": secret,
        "HTTPS_PROXY": f"https://credential:{secret}@proxy.test",
    }
    completed = CompletedProcess([], 0, stdout="", stderr="")
    events = []
    with patch.dict("runner.adapters.subprocess.os.environ", inherited, clear=True):
        with patch(
            "runner.adapters.subprocess.SubprocessAdapter._run_command",
            return_value=completed,
        ) as run:
            envelope = task(Engine(adapter.engine_name))
            result = RunnerAgent(
                envelope, tmp_path, events.append, {envelope.engine: adapter}
            ).run()

    environment = run.call_args.args[2]
    assert environment["PATH"] == "safe-path"
    assert environment["SYSTEMROOT"] == "C:\\Windows"
    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == "C:\\browsers"
    assert not {"DATABASE_URL", "SERVICE_TOKEN", "HTTPS_PROXY"} & environment.keys()
    assert secret not in result.model_dump_json()
    assert all(secret not in event.model_dump_json() for event in events)