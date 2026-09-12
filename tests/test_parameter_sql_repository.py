from uuid import uuid4

import pytest

from packages.protocol import Engine
from services.api.automation_sql_repository import SqlAutomationRepository
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.parameter_domain import (
    ParameterEnumOption, ParameterInputType, ScriptParameterDefinition,
)
from services.api.parameter_repository import ParameterEnumConflict
from services.api.parameter_sql_repository import SqlParameterRepository


def test_sql_parameter_catalog_and_definitions_are_persisted(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'parameters.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    scripts = SqlAutomationRepository(sessions)
    repository = SqlParameterRepository(sessions)
    project_id = uuid4()
    script = scripts.create_script(
        project_id, "Parameters", "", Engine.PYTEST, "test_parameters.py",
        "artifact:parameters", "a" * 64, 60,
    )
    enum_set = repository.create_enum_set(
        project_id, "Enabled", "", [
            ParameterEnumOption("False", False), ParameterEnumOption("Zero", 0),
        ],
    )
    expected = [ScriptParameterDefinition(
        "enabled", "Enabled", ParameterInputType.ENUM, True,
        enum_set.id, False, 0,
    )]

    repository.replace_script_parameters(script.id, expected)
    reloaded = SqlParameterRepository(sessions)

    assert reloaded.list_enum_sets(project_id) == [enum_set]
    assert reloaded.list_script_parameters(script.id) == expected
    with pytest.raises(ParameterEnumConflict, match="referenced"):
        reloaded.delete_enum_set(enum_set.id)
    reloaded.replace_script_parameters(script.id, [])
    reloaded.delete_enum_set(enum_set.id)
    assert reloaded.list_enum_sets(project_id) == []
    engine.dispose()


def test_sql_enum_name_and_version_conflicts(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'conflicts.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    repository = SqlParameterRepository(create_session_factory(engine))
    project_id = uuid4()
    options = [ParameterEnumOption("A", "a")]
    created = repository.create_enum_set(project_id, "Status", "", options)

    with pytest.raises(ParameterEnumConflict):
        repository.create_enum_set(project_id, "Status", "", options)
    updated = repository.update_enum_set(
        created.id, expected_version=0, name="Status", description="updated",
        options=options,
    )
    assert updated.state_version == 1
    with pytest.raises(ParameterEnumConflict, match="state version mismatch"):
        repository.update_enum_set(
            created.id, expected_version=0, name="Status", description="stale",
            options=options,
        )
    engine.dispose()