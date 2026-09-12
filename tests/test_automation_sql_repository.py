from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine as SqlEngine
from sqlalchemy.orm import sessionmaker

from packages.protocol import Engine
from services.api.automation_domain import AutomationScriptStatus
from services.api.automation_repository import ScriptLinkConflict
from services.api.automation_sql_repository import SqlAutomationRepository
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.quality_domain import CaseStepRecord, TestCasePriority as CasePriority
from services.api.quality_sql_repository import SqlQualityRepository


DIGEST = "c" * 64


@pytest.fixture
def database(tmp_path) -> Iterator[tuple[SqlEngine, sessionmaker]]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'automation.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_scripts_revisions_and_links_are_persisted(database) -> None:
    _, sessions = database
    quality = SqlQualityRepository(sessions)
    repository = SqlAutomationRepository(sessions)
    project_id = uuid4()
    case = quality.create_test_case(
        project_id, "Checkout", "", "Cart has items",
        CasePriority.CRITICAL,
        (CaseStepRecord(1, "Pay", "Order is created"),),
    )
    script = repository.create_script(
        project_id, "Checkout API", "", Engine.HTTP, "request.json",
        "artifact:checkout-v1", DIGEST, 30,
    )
    active = repository.update_script(
        script.id, expected_version=0, name=script.name,
        description="Published", entrypoint=script.entrypoint,
        source_ref="artifact:checkout-v2", content_digest="d" * 64,
        timeout_seconds=60, status=AutomationScriptStatus.ACTIVE,
    )

    _, replayed = repository.link_test_case(case.id, script.id)
    _, repeated = repository.link_test_case(case.id, script.id)

    assert replayed is False
    assert repeated is True
    assert SqlAutomationRepository(sessions).get_script(script.id) == active
    assert repository.list_test_case_scripts(case.id) == [active]
    assert [item.revision for item in repository.list_revisions(script.id)] == [1, 2]

    archived = repository.update_script(
        script.id, expected_version=active.state_version, name=active.name,
        description=active.description, entrypoint=active.entrypoint,
        source_ref=active.source_ref, content_digest=active.content_digest,
        timeout_seconds=active.timeout_seconds, status=AutomationScriptStatus.ARCHIVED,
    )
    restored = repository.update_script(
        script.id, expected_version=archived.state_version, name=archived.name,
        description=archived.description, entrypoint=archived.entrypoint,
        source_ref=archived.source_ref, content_digest=archived.content_digest,
        timeout_seconds=archived.timeout_seconds, status=AutomationScriptStatus.ACTIVE,
    )
    assert SqlAutomationRepository(sessions).get_script(script.id) == restored
    assert [item.revision for item in repository.list_revisions(script.id)] == [1, 2, 3, 4]

    foreign = repository.create_script(
        uuid4(), "Foreign", "", Engine.PYTEST, "test_foreign.py",
        "artifact:foreign", DIGEST, 60,
    )
    with pytest.raises(ScriptLinkConflict, match="different projects"):
        repository.link_test_case(case.id, foreign.id)