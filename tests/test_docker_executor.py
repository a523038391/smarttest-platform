from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from packages.protocol import (
    Engine, EventEnvelope, EventType, ResultEnvelope, ResultOutcome, SecretReference,
    TaskEnvelope,
)
from services.api.environment_domain import ConfigurationCategory
from services.api.environment_repository import ResolvedSecretValue
from services.docker_executor import (
    DockerContainerSpec,
    DockerExecutionError,
    DockerExecutor,
    DockerRunResult,
    SubprocessDockerClient,
)
from services.secret_broker import SecretCapability
from services.secret_files import SecretFileStager


def make_task(deadline: datetime | None = None) -> TaskEnvelope:
    now = datetime.now(timezone.utc)
    return TaskEnvelope(
        run_id=uuid4(), attempt_id=uuid4(), task_id=uuid4(), tenant_id=uuid4(),
        project_id=uuid4(), idempotency_key="docker-test", engine=Engine.PYTEST,
        created_at=now - timedelta(seconds=1), deadline=deadline or now + timedelta(minutes=1),
        entrypoint="test_case.py",
    )


def with_secret(task: TaskEnvelope) -> TaskEnvelope:
    reference = SecretReference(
        category="environment_variable", name="TOKEN", secret_ref=uuid4()
    )
    return task.model_copy(update={
        "environment_id": uuid4(), "environment_revision": 1,
        "secret_references": (reference,),
    })


def test_executor_streams_events_and_returns_result(tmp_path: Path) -> None:
    task = make_task()
    event = EventEnvelope(
        run_id=task.run_id, attempt_id=task.attempt_id, engine=task.engine,
        event_id=uuid4(), seq=0, type=EventType.STARTED,
    )
    expected = ResultEnvelope(
        run_id=task.run_id, attempt_id=task.attempt_id, engine=task.engine,
        outcome=ResultOutcome.SUCCEEDED, duration_ms=4,
    )
    client = Mock()

    def run(spec, payload, consume, timeout):
        assert json.loads(payload)["attempt_id"] == str(task.attempt_id)
        assert spec.network == "none"
        assert ("smarttest.run_id", str(task.run_id)) in spec.labels
        assert 0 < timeout <= 60
        consume(json.dumps({"kind": "event", "data": event.model_dump(mode="json")}))
        consume(json.dumps({"kind": "result", "data": expected.model_dump(mode="json")}))
        return DockerRunResult(0)

    client.run.side_effect = run
    events = []
    result = DockerExecutor("runner@sha256:" + "a" * 64, tmp_path, client).execute(task, events.append)

    assert result == expected
    assert events == [event]


def test_executor_converts_deadline_and_container_failures(tmp_path: Path) -> None:
    client = Mock()
    expired = make_task(datetime.now(timezone.utc) - timedelta(milliseconds=1))
    result = DockerExecutor("runner:latest", tmp_path, client).execute(expired, Mock())
    assert result.outcome is ResultOutcome.TIMED_OUT
    client.run.assert_not_called()

    client.run.return_value = DockerRunResult(137, timed_out=True)
    result = DockerExecutor("runner:latest", tmp_path, client).execute(make_task(), Mock())
    assert result.outcome is ResultOutcome.TIMED_OUT


def test_subprocess_client_builds_hardened_command_and_uses_stdin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    secret_directory = tmp_path / "secret-directory"
    workspace.mkdir()
    secret_directory.mkdir()
    plaintext = "never-in-docker-command"
    (secret_directory / "TOKEN").write_text(plaintext, encoding="utf-8")
    process = Mock()
    process.stdin = Mock()
    process.stdout = iter([])
    process.stderr.read.side_effect = ["", ""]
    process.wait.return_value = 0
    process.poll.return_value = 0
    with (
        patch("services.docker_executor.subprocess.Popen", return_value=process) as popen,
        patch("services.docker_executor.subprocess.run"),
    ):
        client = SubprocessDockerClient()
        result = client.run(
            DockerContainerSpec(
                "runner:latest", "smarttest-123", workspace,
                secret_directory=secret_directory,
            ),
            '{"task":"stdin-only"}\n', Mock(), 1,
        )

    assert result.exit_code == 0
    command = popen.call_args.args[0]
    assert "--read-only" in command and "--cap-drop" in command
    assert "no-new-privileges" in command and "--network" in command
    assert f"type=bind,src={secret_directory.resolve()},dst=/run/secrets,readonly" in command
    assert not any(plaintext in argument or "stdin-only" in argument for argument in command)
    process.stdin.write.assert_called_once_with('{"task":"stdin-only"}\n')


def test_executor_secret_lifecycle_mounts_without_transport_leaks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    staging_root = tmp_path / "staging"
    workspace.mkdir()
    staging_root.mkdir()
    task = with_secret(make_task())
    plaintext = "never-transport-this"
    token = "never-transport-token"
    capability = SecretCapability(token)
    resolved = ResolvedSecretValue(
        ConfigurationCategory.ENVIRONMENT_VARIABLE, "TOKEN",
        task.secret_references[0].secret_ref, plaintext,
    )
    broker = Mock()
    broker.issue.return_value = capability
    broker.redeem.return_value = (resolved,)
    client = Mock()
    expected = ResultEnvelope(
        run_id=task.run_id, attempt_id=task.attempt_id, engine=task.engine,
        outcome=ResultOutcome.SUCCEEDED, duration_ms=1,
    )

    def run(spec, payload, consume, _timeout):
        assert spec.secret_directory.is_dir()
        assert (spec.secret_directory / "environment_variable" / "TOKEN").read_text(
            encoding="utf-8"
        ) == plaintext
        serialized = payload + repr(spec.labels)
        assert plaintext not in serialized and token not in serialized
        consume(json.dumps({"kind": "result", "data": expected.model_dump(mode="json")}))
        return DockerRunResult(0)

    client.run.side_effect = run
    executor = DockerExecutor(
        "runner:latest", workspace, client, secret_broker=broker,
        secret_file_stager=SecretFileStager(
            staging_root, verifier=lambda root: None,
            ownership_operation=lambda path, uid, gid: None,
        ),
        runner_id="runner-a",
    )
    assert executor.execute(task, Mock()) == expected
    assert not list(staging_root.iterdir())
    broker.issue.assert_called_once_with(task, "runner-a")
    broker.redeem.assert_called_once_with(capability, task, "runner-a")
    broker.revoke.assert_called_once_with(capability)


def test_executor_fails_closed_when_secret_services_are_missing(tmp_path: Path) -> None:
    client = Mock()
    with pytest.raises(DockerExecutionError, match="not configured"):
        DockerExecutor("runner:latest", tmp_path, client).execute(
            with_secret(make_task()), Mock()
        )
    client.run.assert_not_called()


def test_secret_and_artifact_cleanup_survive_container_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    staging_root = tmp_path / "staging"
    spool_root = tmp_path / "spool"
    workspace.mkdir()
    staging_root.mkdir()
    task = with_secret(make_task())
    capability = SecretCapability("opaque-token")
    broker = Mock()
    broker.issue.return_value = capability
    broker.redeem.return_value = (ResolvedSecretValue(
        ConfigurationCategory.ENVIRONMENT_VARIABLE, "TOKEN",
        task.secret_references[0].secret_ref, "plaintext",
    ),)
    broker.revoke.side_effect = RuntimeError("best effort")
    client = Mock()
    client.run.side_effect = RuntimeError("container boom")
    executor = DockerExecutor(
        "runner:latest", workspace, client, artifact_spool_root=spool_root,
        secret_broker=broker,
        secret_file_stager=SecretFileStager(
            staging_root, verifier=lambda root: None,
            ownership_operation=lambda path, uid, gid: None,
        ),
        runner_id="runner-a",
    )

    with pytest.raises(RuntimeError, match="container boom"):
        executor.execute(task, Mock())
    assert not list(staging_root.iterdir())
    assert not (spool_root / str(task.attempt_id)).exists()
    broker.revoke.assert_called_once_with(capability)


def test_empty_secret_references_preserve_old_path_without_broker_calls(tmp_path: Path) -> None:
    broker = Mock()
    stager = Mock()
    client = Mock(return_value=None)

    def run(spec, _payload, _consume, _timeout):
        assert spec.secret_directory is None
        return DockerRunResult(1)

    client.run.side_effect = run
    result = DockerExecutor(
        "runner:latest", tmp_path, client, secret_broker=broker,
        secret_file_stager=stager, runner_id="runner-a",
    ).execute(make_task(), Mock())
    assert result.outcome is ResultOutcome.INFRA_ERROR
    broker.issue.assert_not_called()
    broker.redeem.assert_not_called()
    broker.revoke.assert_not_called()
    stager.stage.assert_not_called()


def test_client_rejects_secret_overlap_and_symlink_without_path_leak(tmp_path: Path) -> None:
    client = SubprocessDockerClient()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    overlapping = workspace / "secrets"
    overlapping.mkdir()
    with pytest.raises(ValueError) as overlap_error:
        client._command(DockerContainerSpec(
            "runner:latest", "smarttest-123", workspace,
            secret_directory=overlapping,
        ))
    assert str(overlapping) not in str(overlap_error.value)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "secret-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(ValueError) as symlink_error:
        client._command(DockerContainerSpec(
            "runner:latest", "smarttest-123", workspace,
            secret_directory=link,
        ))
    assert str(link) not in str(symlink_error.value)


def test_executor_cleans_attempt_spool_after_callback(tmp_path: Path) -> None:
    task = make_task()
    client = Mock()
    expected = ResultEnvelope(
        run_id=task.run_id, attempt_id=task.attempt_id, engine=task.engine,
        outcome=ResultOutcome.SUCCEEDED, duration_ms=1,
    )

    def run(spec, _payload, consume, _timeout):
        assert spec.artifact_spool.is_dir()
        (spec.artifact_spool / "result.txt").write_text("result", encoding="utf-8")
        consume(json.dumps({"kind": "result", "data": expected.model_dump(mode="json")}))
        return DockerRunResult(0)

    client.run.side_effect = run
    executor = DockerExecutor(
        "runner:latest", tmp_path, client, artifact_spool_root=tmp_path / "spool"
    )
    assert executor.execute(task, Mock()) == expected
    assert not (tmp_path / "spool" / str(task.attempt_id)).exists()


def test_client_stops_consuming_after_invalid_transport_message(tmp_path: Path) -> None:
    process = Mock()
    process.stdin = Mock()
    process.stdout = iter(["invalid\n", "must-not-be-consumed\n"])
    process.stderr.read.side_effect = [""]
    process.wait.return_value = 0
    process.poll.return_value = 0
    consume = Mock(side_effect=ValueError("invalid"))
    with (
        patch("services.docker_executor.subprocess.Popen", return_value=process),
        patch("services.docker_executor.subprocess.run"),
        pytest.raises(Exception, match="invalid transport message"),
    ):
        SubprocessDockerClient().run(
            DockerContainerSpec("runner:latest", "smarttest-123", tmp_path),
            "{}\n", consume, 1,
        )
    consume.assert_called_once_with("invalid")