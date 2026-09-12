from pathlib import Path

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
            ).scalar_one() == "0014"
            tables = set(inspect(connection).get_table_names())
            assert {
                "projects", "run_specs", "secret_capability_grants",
                "parameter_enum_sets", "automation_script_parameters",
                "data_factory_workflows", "data_factory_runs",
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
            assert {
                key["referred_table"]
                for key in inspect(connection).get_foreign_keys(
                    "automation_script_parameters"
                )
            } == {"automation_scripts", "parameter_enum_sets"}
    finally:
        engine.dispose()