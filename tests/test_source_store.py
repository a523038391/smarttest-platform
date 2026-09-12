import hashlib
from io import BytesIO
from pathlib import Path
import stat
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.automation_schemas import AutomationSourceCreate
from services.api.config import Settings
from services.source_store import (
    LocalSourceStore, MAX_SOURCE_BYTES, SourceStoreError, StoredSource,
)


def project_zip(files: dict[str, str]) -> bytes:
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return content.getvalue()


def test_source_store_put_and_materialize_are_content_addressed(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    project_id = uuid4()
    content = "def test_ok():\n    assert '你好'\n"

    first = store.put(project_id, content)
    second = store.put(project_id, content)
    target = store.materialize(
        project_id, first.source_ref, first.content_digest,
        "tests/test_generated.py", tmp_path / "workspace",
    )

    assert first == second
    assert first.content_digest == hashlib.sha256(content.encode()).hexdigest()
    assert first.size_bytes == len(content.encode("utf-8"))
    assert target.read_text(encoding="utf-8") == content
    assert target == tmp_path / "workspace" / "tests" / "test_generated.py"


@pytest.mark.parametrize(
    "entrypoint",
    [
        "../escape.py", "/absolute.py", "tests\\case.py", "a/../../case.py",
        "a//b.py", "C:/escape.py",
    ],
)
def test_materialize_rejects_unsafe_entrypoints(
    tmp_path: Path, entrypoint: str
) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    project_id = uuid4()
    saved = store.put(project_id, "safe")

    with pytest.raises(SourceStoreError, match="entrypoint"):
        store.materialize(
            project_id, saved.source_ref, saved.content_digest,
            entrypoint, tmp_path / "workspace",
        )


def test_materialize_rejects_cross_project_digest_and_tampering(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    project_id = uuid4()
    saved = store.put(project_id, "original")

    with pytest.raises(SourceStoreError, match="different project"):
        store.materialize(
            uuid4(), saved.source_ref, saved.content_digest, "case.py", tmp_path / "one"
        )
    with pytest.raises(SourceStoreError, match="do not match"):
        store.materialize(
            project_id, saved.source_ref, "0" * 64, "case.py", tmp_path / "two"
        )

    stored_file = next(path for path in store.root.rglob("*") if path.is_file())
    stored_file.write_text("tampered", encoding="utf-8")
    with pytest.raises(SourceStoreError, match="digest"):
        store.materialize(
            project_id, saved.source_ref, saved.content_digest,
            "case.py", tmp_path / "three",
        )


def test_source_upload_response_never_contains_source(tmp_path: Path) -> None:
    content = "print('source-marker')"
    app = create_app(
        settings=Settings(auth_required=False),
        source_store=LocalSourceStore(tmp_path / "sources"),
    )
    response = TestClient(app).post(
        "/api/v1/automation-sources",
        json={"project_id": str(uuid4()), "content": content},
    )

    assert response.status_code == 201
    assert set(response.json()) == {"source_ref", "content_digest", "size_bytes"}
    assert content not in response.text
    assert "source-marker" not in response.text
    assert content not in repr(AutomationSourceCreate(project_id=uuid4(), content=content))


def test_source_size_limit_uses_utf8_bytes(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    with pytest.raises(SourceStoreError, match="1 MiB"):
        store.put(uuid4(), "界" * (MAX_SOURCE_BYTES // 3 + 1))

    response = TestClient(create_app(
        settings=Settings(auth_required=False), source_store=store,
    )).post("/api/v1/automation-sources", json={
        "project_id": str(uuid4()), "content": "界" * (MAX_SOURCE_BYTES // 3 + 1),
    })
    assert response.status_code == 422


def test_host_source_is_disabled_by_default(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    with pytest.raises(SourceStoreError, match="disabled"):
        store.register_host(uuid4(), str(tmp_path), str(tmp_path / "python.exe"))


def test_host_source_revalidates_paths_and_entrypoint(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    interpreter = project / ".venv" / "Scripts" / "python.exe"
    entrypoint = project / "tests" / "test_local.py"
    interpreter.parent.mkdir(parents=True)
    entrypoint.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    entrypoint.write_text("pass", encoding="utf-8")
    store = LocalSourceStore(
        tmp_path / "sources", host_execution_enabled=True,
        host_project_roots=(allowed,),
    )
    project_id = uuid4()

    saved = store.register_host(project_id, str(project), str(interpreter))
    resolved = store.resolve_host_reference(
        project_id, saved.source_ref, saved.content_digest, "tests/test_local.py"
    )

    assert saved.source_ref.startswith("host-source:v1:")
    assert str(project) not in saved.source_ref
    assert resolved.project_directory == project.resolve()
    assert resolved.python_executable == interpreter.resolve()
    with pytest.raises(SourceStoreError, match="entrypoint"):
        store.resolve_host_reference(
            project_id, saved.source_ref, saved.content_digest, "tests/missing.py"
        )


def test_host_source_rejects_project_outside_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    interpreter = outside / ".venv" / "python.exe"
    allowed.mkdir()
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    store = LocalSourceStore(
        tmp_path / "sources", host_execution_enabled=True,
        host_project_roots=(allowed,),
    )

    with pytest.raises(SourceStoreError, match="outside"):
        store.register_host(uuid4(), str(outside), str(interpreter))


def test_zip_project_materializes_all_files_and_requires_entrypoint(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    project_id = uuid4()
    archive = project_zip({
        "tests/test_login.py": "from pages.login import Login\ndef test_login(): assert Login.ok\n",
        "pages/__init__.py": "",
        "pages/login.py": "class Login:\n    ok = True\n",
    })

    saved = store.put_archive(project_id, archive)
    target = store.materialize(
        project_id, saved.source_ref, saved.content_digest,
        "tests/test_login.py", tmp_path / "workspace",
    )

    assert saved.source_ref.startswith("local-source:v2:")
    assert target.read_text(encoding="utf-8").startswith("from pages.login")
    assert (tmp_path / "workspace/pages/login.py").is_file()
    with pytest.raises(SourceStoreError, match="does not exist"):
        store.materialize(
            project_id, saved.source_ref, saved.content_digest,
            "missing.py", tmp_path / "other-workspace",
        )


@pytest.mark.parametrize("unsafe_name", ["../escape.py", "/escape.py", "C:/escape.py"])
def test_zip_project_rejects_unsafe_paths(tmp_path: Path, unsafe_name: str) -> None:
    with pytest.raises(SourceStoreError, match="unsafe path"):
        LocalSourceStore(tmp_path / "sources").put_archive(
            uuid4(), project_zip({unsafe_name: "bad"})
        )


def test_zip_project_rejects_links_and_case_conflicts(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    conflicting = project_zip({"pages/Login.py": "one", "pages/login.py": "two"})
    with pytest.raises(SourceStoreError, match="case-conflicting"):
        store.put_archive(uuid4(), conflicting)

    content = BytesIO()
    link = ZipInfo("linked.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(content, "w") as archive:
        archive.writestr(link, "target.py")
    with pytest.raises(SourceStoreError, match="links"):
        store.put_archive(uuid4(), content.getvalue())


def test_zip_upload_api_returns_project_reference(tmp_path: Path) -> None:
    project_id = uuid4()
    app = create_app(
        settings=Settings(auth_required=False),
        source_store=LocalSourceStore(tmp_path / "sources"),
    )
    response = TestClient(app).post(
        f"/api/v1/automation-sources/zip?project_id={project_id}",
        content=project_zip({"tests/test_ok.py": "def test_ok(): assert True"}),
        headers={"Content-Type": "application/zip"},
    )

    assert response.status_code == 201
    assert response.json()["source_ref"].startswith("local-source:v2:")

    wrong_type = TestClient(app).post(
        f"/api/v1/automation-sources/zip?project_id={project_id}",
        content=b"not-a-zip", headers={"Content-Type": "application/octet-stream"},
    )
    assert wrong_type.status_code == 422
    assert wrong_type.json()["code"] == "automation_source_invalid"


def test_host_source_api_registers_active_project_without_disclosing_paths(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    project_directory = allowed / "project"
    interpreter = project_directory / ".venv" / "python.exe"
    entrypoint = project_directory / "tests" / "test_ui.py"
    interpreter.parent.mkdir(parents=True)
    entrypoint.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    entrypoint.write_text("pass", encoding="utf-8")
    client = TestClient(create_app(settings=Settings(
        auth_required=False,
        source_root=str(tmp_path / "sources"),
        host_execution_enabled=True,
        host_project_roots=(str(allowed),),
    )))
    project = client.post("/api/v1/projects", json={
        "name": "Local UI", "description": "",
    }).json()

    source = client.post("/api/v1/automation-sources/host", json={
        "project_id": project["id"],
        "project_directory": str(project_directory),
        "python_executable": str(interpreter),
    })
    source_fields = {
        name: source.json()[name] for name in ("source_ref", "content_digest")
    }
    created = client.post("/api/v1/automation-scripts", json={
        "project_id": project["id"], "name": "UI", "engine": "playwright",
        "entrypoint": "tests/test_ui.py", **source_fields,
    })
    rejected = client.post("/api/v1/automation-scripts", json={
        "project_id": project["id"], "name": "HTTP", "engine": "http",
        "entrypoint": "tests/test_ui.py", **source_fields,
    })

    assert source.status_code == 201
    assert str(project_directory) not in source.text
    assert str(interpreter) not in source.text
    assert created.status_code == 201
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "automation_source_invalid"


def test_script_create_validates_zip_entrypoint_before_persisting(tmp_path: Path) -> None:
    project_id = uuid4()
    store = LocalSourceStore(tmp_path / "sources")
    saved = store.put_archive(project_id, project_zip({"tests/test_ok.py": "pass"}))
    client = TestClient(create_app(
        settings=Settings(auth_required=False), source_store=store,
    ))

    response = client.post("/api/v1/automation-scripts", json={
        "project_id": str(project_id), "name": "missing", "engine": "pytest",
        "entrypoint": "tests/missing.py", "source_ref": saved.source_ref,
        "content_digest": saved.content_digest,
    })

    assert response.status_code == 422
    assert response.json()["code"] == "automation_source_invalid"


def test_git_import_archives_fetched_revision_without_shell(tmp_path: Path, monkeypatch) -> None:
    store = LocalSourceStore(tmp_path / "sources")
    commands: list[list[str]] = []

    def fake_git(arguments: list[str]) -> None:
        commands.append(arguments)

    monkeypatch.setattr(store, "_run_git", fake_git)
    monkeypatch.setattr(
        store, "_run_git_archive",
        lambda arguments: (
            commands.append(arguments) or project_zip({"tests/test_git.py": "pass"})
        ),
    )
    monkeypatch.setattr("services.source_store.socket.getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("140.82.121.4", 443)),
    ])
    saved = store.import_git(uuid4(), "https://github.com/example/public.git", "main")

    assert saved.source_ref.startswith("local-source:v2:")
    assert any(command[-2:] == ["origin", "main"] and "fetch" in command
               for command in commands)
    assert all(isinstance(command, list) for command in commands)


def test_git_import_api_uses_requested_revision(tmp_path: Path, monkeypatch) -> None:
    project_id = uuid4()
    store = LocalSourceStore(tmp_path / "sources")
    expected = StoredSource(
        f"local-source:v2:{project_id}:sha256:{'a' * 64}", "a" * 64, 123,
    )
    observed = {}

    def import_git(project, repository_url, git_ref):
        observed.update(project=project, url=repository_url, ref=git_ref)
        return expected

    monkeypatch.setattr(store, "import_git", import_git)
    response = TestClient(create_app(
        settings=Settings(auth_required=False), source_store=store,
    )).post("/api/v1/automation-sources/git", json={
        "project_id": str(project_id),
        "repository_url": "https://github.com/example/repository.git",
        "git_ref": "release/v1",
    })

    assert response.status_code == 201
    assert response.json() == {
        "source_ref": expected.source_ref,
        "content_digest": expected.content_digest,
        "size_bytes": 123,
    }
    assert observed == {
        "project": project_id,
        "url": "https://github.com/example/repository.git",
        "ref": "release/v1",
    }


@pytest.mark.parametrize("url", [
    "http://github.com/example/repo.git",
    "https://user:password@github.com/example/repo.git",
    "https://127.0.0.1/repo.git",
    "https://github.com:bad/repo.git",
])
def test_git_import_rejects_unsafe_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(SourceStoreError, match="Git"):
        LocalSourceStore(tmp_path / "sources").import_git(uuid4(), url)