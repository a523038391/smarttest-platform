from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from packages.protocol import (
    Engine, EventEnvelope, EventType, ResultEnvelope, ResultOutcome, TaskEnvelope,
)
from services.api.app import create_app
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.domain import RunState
from services.api.repository import RunRepository
from services.api.sql_repository import SqlRunRepository
from services.artifacts import ArtifactCollector, LocalArtifactStore, PresignedArtifactStore
from services.runner_manager import RunnerManager


def _task(run_id, attempt_id) -> TaskEnvelope:
    now = datetime.now(timezone.utc)
    return TaskEnvelope(
        run_id=run_id, attempt_id=attempt_id, task_id=uuid4(), tenant_id=uuid4(),
        project_id=uuid4(), idempotency_key="artifact-test", engine=Engine.PYTEST,
        created_at=now, deadline=now + timedelta(minutes=1), entrypoint="test.py",
    )


def _execute_with_artifact(repository, spool: Path, store: LocalArtifactStore):
    run = repository.create(Engine.PYTEST, {})
    repository.transition(run.id, RunState.QUEUED)
    repository.transition(run.id, RunState.DISPATCHING)
    attempt_id = uuid4()
    task = _task(run.id, attempt_id)
    source = spool / str(attempt_id) / "shots" / "failure.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png-content")

    def execute(envelope, sink):
        sink(EventEnvelope(
            run_id=envelope.run_id, attempt_id=envelope.attempt_id,
            engine=envelope.engine, event_id=uuid4(), seq=0,
            type=EventType.SCREENSHOT,
            payload={"relative_path": "shots/failure.png", "artifact_type": "screenshot"},
        ))
        return ResultEnvelope(
            run_id=envelope.run_id, attempt_id=envelope.attempt_id,
            engine=envelope.engine, outcome=ResultOutcome.SUCCEEDED, duration_ms=1,
        )

    collector = ArtifactCollector(repository, store, spool)
    RunnerManager(repository, execute, artifact_collector=collector).execute(task)
    return run, task


def test_manager_uploads_artifact_and_rewrites_event(tmp_path: Path) -> None:
    repository = RunRepository()
    store = LocalArtifactStore(tmp_path / "objects")
    run, _ = _execute_with_artifact(repository, tmp_path / "spool", store)

    artifact = repository.list_artifacts(run.id)[0]
    event = repository.list_events(run.id)[0]
    assert artifact.name == "failure.png"
    assert store.local_path(artifact.storage_key).read_bytes() == b"png-content"
    assert "relative_path" not in event.payload
    assert event.payload["url"].endswith(f"/{artifact.id}/content")


def test_artifact_api_lists_and_downloads_local_content(tmp_path: Path) -> None:
    repository = RunRepository()
    store = LocalArtifactStore(tmp_path / "objects")
    run, _ = _execute_with_artifact(repository, tmp_path / "spool", store)
    client = TestClient(create_app(repository=repository, artifact_store=store))

    listing = client.get(f"/api/v1/runs/{run.id}/artifacts")
    artifact = listing.json()["items"][0]
    content = client.get(artifact["download_url"])

    assert listing.status_code == 200
    assert artifact["name"] == "failure.png"
    assert "storage_key" not in artifact
    assert content.status_code == 200
    assert content.content == b"png-content"


def test_sql_repository_persists_artifact_metadata(tmp_path: Path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'artifacts.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    try:
        repository = SqlRunRepository(create_session_factory(engine))
        store = LocalArtifactStore(tmp_path / "objects")
        run, _ = _execute_with_artifact(repository, tmp_path / "spool", store)
        artifact = repository.list_artifacts(run.id)[0]
        assert repository.get_artifact(artifact.id) == artifact
        assert repository.get(run.id).state is RunState.SUCCEEDED
    finally:
        engine.dispose()


def test_presigned_store_rejects_untrusted_host_without_request(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("report", encoding="utf-8")
    session = Mock()
    store = PresignedArtifactStore(
        lambda _key, _type: "https://metadata.invalid/upload?signature=redacted",
        lambda _key: "https://objects.example/download?signature=redacted",
        {"objects.example"},
        session,
    )

    try:
        store.put(source, "key", "text/plain")
    except Exception as exc:
        assert "URL is not allowed" in str(exc)
    else:
        raise AssertionError("untrusted object storage host was accepted")
    session.put.assert_not_called()


def test_presigned_store_sends_content_length(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_bytes(b"report")
    session = Mock()
    session.put.return_value.status_code = 200
    store = PresignedArtifactStore(
        lambda _key, _type: "https://objects.example/upload?signature=redacted",
        lambda _key: "https://objects.example/download?signature=redacted",
        {"objects.example"},
        session,
    )

    assert store.put(source, "key", "text/plain") == 6
    assert session.put.call_args.kwargs["headers"]["Content-Length"] == "6"


def test_presigned_store_enforces_host_side_size_limit(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"1234")
    session = Mock()
    store = PresignedArtifactStore(
        lambda _key, _type: "https://objects.example/upload",
        lambda _key: "https://objects.example/download",
        {"objects.example"},
        session,
        maximum_bytes=3,
    )

    try:
        store.put(source, "key", "application/octet-stream")
    except Exception as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("oversized artifact was accepted")
    session.put.assert_not_called()