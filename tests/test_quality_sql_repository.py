from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from services.api.database import Base, create_database_engine, create_session_factory
from services.api.quality_domain import (
    CaseStepRecord,
    RequirementStatus,
    TestCasePriority as CasePriority,
    TestCaseStatus as CaseStatus,
)
from services.api.quality_repository import AssetVersionConflict, TraceLinkConflict
from services.api.quality_sql_repository import SqlQualityRepository


@pytest.fixture
def database(tmp_path) -> Iterator[tuple[Engine, sessionmaker]]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'quality.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_requirement_is_persisted_with_immutable_revisions(database) -> None:
    _, sessions = database
    repository = SqlQualityRepository(sessions)
    project_id = uuid4()
    requirement = repository.create_requirement(project_id, "Checkout", "Initial")

    active = repository.update_requirement(
        requirement.id, "Checkout", "Updated", RequirementStatus.ACTIVE, 0
    )

    assert SqlQualityRepository(sessions).get_requirement(requirement.id) == active
    revisions = repository.list_requirement_revisions(requirement.id)
    assert [(item.revision, item.description) for item in revisions] == [
        (1, "Initial"), (2, "Updated")
    ]
    with pytest.raises(AssetVersionConflict):
        repository.update_requirement(
            requirement.id, "Stale", "", RequirementStatus.ACTIVE, 0
        )


def test_test_case_and_trace_links_are_persisted(database) -> None:
    _, sessions = database
    repository = SqlQualityRepository(sessions)
    project_id = uuid4()
    requirement = repository.create_requirement(project_id, "Checkout", "")
    case = repository.create_test_case(
        project_id,
        "Card payment",
        "",
        "Cart has items",
        CasePriority.CRITICAL,
        (CaseStepRecord(1, "Pay", "Order is created"),),
    )
    ready = repository.update_test_case(
        case.id,
        expected_version=0,
        title=case.title,
        description=case.description,
        preconditions=case.preconditions,
        priority=case.priority,
        status=CaseStatus.READY,
        steps=case.steps,
    )

    _, replayed = repository.link_requirement_test_case(requirement.id, case.id)
    _, repeated = repository.link_requirement_test_case(requirement.id, case.id)

    assert replayed is False
    assert repeated is True
    assert repository.list_requirement_test_cases(requirement.id) == [ready]
    assert len(repository.list_test_case_revisions(case.id)) == 2

    foreign = repository.create_test_case(
        uuid4(), "Foreign", "", "", CasePriority.LOW, ()
    )
    with pytest.raises(TraceLinkConflict):
        repository.link_requirement_test_case(requirement.id, foreign.id)