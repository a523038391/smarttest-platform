from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from packages.protocol import Engine, ResultEnvelope, ResultOutcome
from services.api.app import create_app
from services.api.automation_domain import AutomationScriptStatus
from services.api.config import Settings
from services.api.dispatcher import DatabaseRunDispatcher
from services.api.domain import RUN_TERMINAL, RunState
from services.api.test_plan_schemas import SINGLE_TENANT_ID
from services.source_store import LocalSourceStore


class SuccessfulExecutor:
    def __init__(self, workspace: Path, observed: list[str]) -> None:
        self.workspace = workspace
        self.observed = observed

    def execute(self, task, _sink) -> ResultEnvelope:
        self.observed.append(
            (self.workspace / task.entrypoint).read_text(encoding="utf-8")
        )
        return ResultEnvelope(
            run_id=task.run_id,
            attempt_id=task.attempt_id,
            engine=task.engine,
            outcome=ResultOutcome.SUCCEEDED,
            duration_ms=1,
        )


def wait_for_terminal(app, run_ids: list[str], timeout: float = 3) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if all(
            app.state.run_repository.get(uuid4_from(run_id)).state in RUN_TERMINAL
            for run_id in run_ids
        ):
            return
        sleep(0.01)
    states = [
        app.state.run_repository.get(uuid4_from(run_id)).state for run_id in run_ids
    ]
    raise AssertionError(f"runs did not finish: {states}")


def uuid4_from(value: str) -> UUID:
    return UUID(value)


def test_local_runner_executes_plan_to_terminal_and_refills_capacity(tmp_path: Path) -> None:
    observed: list[str] = []
    settings = Settings(
        auto_dispatch=True,
        local_runner_enabled=True,
        auth_required=False,
        source_root=str(tmp_path / "sources"),
        runner_work_root=str(tmp_path / "work"),
        artifact_root=str(tmp_path / "artifacts"),
        runner_max_workers=1,
        runner_poll_interval_seconds=0.01,
    )
    app = create_app(
        settings=settings,
        executor_factory=lambda workspace, _spool: SuccessfulExecutor(
            workspace, observed
        ),
    )
    project_id = uuid4()
    source = "def test_generated():\n    assert True\n"
    saved = app.state.source_store.put(project_id, source)
    script = app.state.automation_repository.create_script(
        project_id, "generated", "", Engine.PYTEST, "tests/test_generated.py",
        saved.source_ref, saved.content_digest, 30,
    )
    script = app.state.automation_repository.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=script.content_digest, timeout_seconds=30,
        status=AutomationScriptStatus.ACTIVE,
    )

    with TestClient(app) as client:
        created = client.post("/api/v1/test-plans", json={
            "project_id": str(project_id), "name": "local closure",
            "max_parallel": 1,
            "items": [{
                "script_id": str(script.id), "script_revision": script.revision,
                "data_rows": [
                    {"row_key": "one", "values": {"value": 1}},
                    {"row_key": "two", "values": {"value": 2}},
                ],
            }],
        })
        assert created.status_code == 201
        assert created.json()["tenant_id"] == str(SINGLE_TENANT_ID)
        body = created.json()
        activated = client.put(f"/api/v1/test-plans/{body['id']}", json={
            "name": body["name"], "description": body["description"],
            "items": body["items"], "max_parallel": 1,
            "status": "ACTIVE", "state_version": body["state_version"],
        })
        assert activated.status_code == 200
        execution = client.post(
            f"/api/v1/test-plans/{body['id']}/executions",
            headers={"Idempotency-Key": "local-runner-test"},
        )
        assert execution.status_code == 201
        run_ids = execution.json()["run_ids"]
        wait_for_terminal(app, run_ids)

        assert [
            app.state.run_repository.get(uuid4_from(run_id)).state
            for run_id in run_ids
        ] == [RunState.SUCCEEDED, RunState.SUCCEEDED]
        assert len(observed) == 2 and set(observed) == {source}
        assert all(
            len(app.state.run_repository.list_attempts(uuid4_from(run_id))) == 1
            for run_id in run_ids
        )
        assert isinstance(app.state.run_dispatcher, DatabaseRunDispatcher)
        assert app.state.local_runner_service.running

    assert not app.state.local_runner_service.running


def test_local_runner_converges_run_without_run_spec(tmp_path: Path) -> None:
    app = create_app(settings=Settings(
        auto_dispatch=True, local_runner_enabled=True, auth_required=False,
        source_root=str(tmp_path / "sources"),
        runner_work_root=str(tmp_path / "work"),
        artifact_root=str(tmp_path / "artifacts"),
        runner_poll_interval_seconds=0.01,
    ), executor_factory=lambda *_: SuccessfulExecutor(tmp_path, []))

    with TestClient(app) as client:
        response = client.post("/api/v1/runs", json={"engine": "pytest"})
        assert response.status_code == 201
        wait_for_terminal(app, [response.json()["id"]])
        assert app.state.run_repository.get(
            uuid4_from(response.json()["id"])
        ).state is RunState.INFRA_ERROR


def test_local_runner_materializes_multi_file_project(tmp_path: Path) -> None:
    from io import BytesIO
    from zipfile import ZipFile

    observed: list[str] = []
    app = create_app(
        settings=Settings(
            auto_dispatch=True, local_runner_enabled=True, auth_required=False,
            source_root=str(tmp_path / "sources"), runner_work_root=str(tmp_path / "work"),
            artifact_root=str(tmp_path / "artifacts"), runner_poll_interval_seconds=0.01,
        ),
        executor_factory=lambda workspace, _spool: SuccessfulExecutor(workspace, observed),
    )
    project_id = uuid4()
    packed = BytesIO()
    with ZipFile(packed, "w") as archive:
        archive.writestr("tests/test_project.py", "from helpers.value import VALUE\n")
        archive.writestr("helpers/value.py", "VALUE = 42\n")
    saved = app.state.source_store.put_archive(project_id, packed.getvalue())
    script = app.state.automation_repository.create_script(
        project_id, "project", "", Engine.PYTEST, "tests/test_project.py",
        saved.source_ref, saved.content_digest, 30,
    )
    script = app.state.automation_repository.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=script.content_digest, timeout_seconds=30,
        status=AutomationScriptStatus.ACTIVE,
    )

    class ProjectExecutor(SuccessfulExecutor):
        def execute(self, task, sink):
            assert (self.workspace / "helpers/value.py").read_text() == "VALUE = 42\n"
            return super().execute(task, sink)

    app.state.local_runner_service._executor_factory = (
        lambda workspace, _spool: ProjectExecutor(workspace, observed)
    )
    with TestClient(app) as client:
        plan = client.post("/api/v1/test-plans", json={
            "project_id": str(project_id), "name": "multi-file", "items": [{
                "script_id": str(script.id), "script_revision": script.revision,
            }],
        }).json()
        active = client.put(f"/api/v1/test-plans/{plan['id']}", json={
            "name": plan["name"], "description": plan["description"],
            "items": plan["items"], "max_parallel": plan["max_parallel"],
            "status": "ACTIVE", "state_version": plan["state_version"],
        }).json()
        execution = client.post(
            f"/api/v1/test-plans/{active['id']}/executions",
            headers={"Idempotency-Key": "multi-file-project"},
        )
        assert execution.status_code == 201
        wait_for_terminal(app, execution.json()["run_ids"])
        assert observed == ["from helpers.value import VALUE\n"]


def test_local_runner_executes_host_source_without_deleting_project(
    tmp_path: Path,
) -> None:
    observed: list[str] = []
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    interpreter = project / ".venv" / "python.exe"
    entrypoint = project / "tests" / "test_host.py"
    interpreter.parent.mkdir(parents=True)
    entrypoint.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    source = "def test_host():\n    assert True\n"
    entrypoint.write_text(source, encoding="utf-8")
    store = LocalSourceStore(
        tmp_path / "sources", host_execution_enabled=True,
        host_project_roots=(allowed,),
    )
    app = create_app(
        settings=Settings(
            auto_dispatch=True, local_runner_enabled=True, auth_required=False,
            source_root=str(tmp_path / "sources"),
            runner_work_root=str(tmp_path / "work"),
            artifact_root=str(tmp_path / "artifacts"),
            runner_poll_interval_seconds=0.01,
        ),
        source_store=store,
        executor_factory=lambda *_: (_ for _ in ()).throw(
            AssertionError("Docker executor must not be selected")
        ),
        host_executor_factory=lambda workspace, _python, _spool: SuccessfulExecutor(
            workspace, observed
        ),
    )
    project_id = uuid4()
    saved = store.register_host(project_id, str(project), str(interpreter))
    script = app.state.automation_repository.create_script(
        project_id, "host", "", Engine.PYTEST, "tests/test_host.py",
        saved.source_ref, saved.content_digest, 30,
    )
    script = app.state.automation_repository.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=script.content_digest, timeout_seconds=30,
        status=AutomationScriptStatus.ACTIVE,
    )

    with TestClient(app) as client:
        plan = client.post("/api/v1/test-plans", json={
            "project_id": str(project_id), "name": "host", "items": [{
                "script_id": str(script.id), "script_revision": script.revision,
            }],
        }).json()
        active = client.put(f"/api/v1/test-plans/{plan['id']}", json={
            "name": plan["name"], "description": plan["description"],
            "items": plan["items"], "max_parallel": plan["max_parallel"],
            "status": "ACTIVE", "state_version": plan["state_version"],
        }).json()
        execution = client.post(
            f"/api/v1/test-plans/{active['id']}/executions",
            headers={"Idempotency-Key": "host-project"},
        )
        wait_for_terminal(app, execution.json()["run_ids"])

    assert observed == [source]
    assert entrypoint.exists()