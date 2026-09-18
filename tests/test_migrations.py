from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_sqlite_upgrades_from_empty_database_to_head(tmp_path, monkeypatch) -> None:
    root = Path(__file__).parents[1]
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(Config(str(root / "alembic.ini")), "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0017"
            tables = set(inspect(connection).get_table_names())
            assert {
                "projects", "run_specs", "secret_capability_grants",
                "parameter_enum_sets", "automation_script_parameters",
                "data_factory_workflows", "data_factory_runs",
                "load_tests", "load_test_runs",
            } <= tables
            run_spec_columns = {
                column["name"]: column for column in inspect(connection).get_columns("run_specs")
            }
            assert run_spec_columns["execution_policy"]["nullable"] is False
            parameter_columns = {
                column["name"]: column
                for column in inspect(connection).get_columns(
                    "automation_script_parameters"
                )
            }
            assert parameter_columns["default_value"]["nullable"] is True
            load_test_columns = {
                column["name"]: column
                for column in inspect(connection).get_columns("load_tests")
            }
            assert {
                "targets", "mode", "request_count", "duration_seconds", "concurrency",
                "interval_ms", "timeout_seconds", "state_version", "traffic_mode",
                "initial_variables", "stop_on_failure",
            } <= load_test_columns.keys()
            assert load_test_columns["targets"]["nullable"] is False
            assert load_test_columns["environment_id"]["nullable"] is True
            assert load_test_columns["traffic_mode"]["nullable"] is False
            assert load_test_columns["initial_variables"]["nullable"] is False
            assert load_test_columns["stop_on_failure"]["nullable"] is False
            assert {
                key["referred_table"]
                for key in inspect(connection).get_foreign_keys("load_tests")
            } == {"projects", "project_environments"}
            assert {
                key["referred_table"]
                for key in inspect(connection).get_foreign_keys(
                    "automation_script_parameters"
                )
            } == {"automation_scripts", "parameter_enum_sets"}
    finally:
        engine.dispose()


def test_scenario_migration_backfills_existing_load_tests(tmp_path, monkeypatch) -> None:
    root = Path(__file__).parents[1]
    database_path = tmp_path / "migration-backfill.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "0016")

    project_id, load_test_id = str(uuid4()), str(uuid4())
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO projects "
            "(id, name, description, status, state_version, created_at, updated_at) "
            "VALUES (:id, 'Existing', '', 'ACTIVE', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"id": project_id})
        connection.execute(text(
            "INSERT INTO load_tests "
            "(id, project_id, name, description, targets, mode, request_count, "
            "duration_seconds, concurrency, interval_ms, timeout_seconds, state_version, "
            "created_at, updated_at) VALUES "
            "(:id, :project_id, 'Existing', '', '[]', 'COUNT', 1, 1, 1, 0, 5, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"id": load_test_id, "project_id": project_id})
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT traffic_mode, initial_variables, stop_on_failure "
                "FROM load_tests WHERE id = :id"
            ), {"id": load_test_id}).one()
            assert row.traffic_mode == "REQUESTS"
            assert row.initial_variables == "{}"
            assert bool(row.stop_on_failure) is True
    finally:
        engine.dispose()