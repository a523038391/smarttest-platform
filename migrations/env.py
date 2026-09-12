import os
from logging.config import fileConfig

from alembic import context

from services.api.database import Base, create_database_engine
from services.api.auth_models import AuthBootstrapModel, UserModel, UserSessionModel
from services.api.automation_models import (
    AutomationScriptModel,
    AutomationScriptRevisionModel,
    TestCaseScriptLinkModel,
)
from services.api.models import AttemptModel, EventModel, RunModel
from services.api.environment_models import (
    EnvironmentModel,
    EnvironmentRevisionModel,
    EnvironmentValueModel,
)
from services.api.data_factory_models import (
    DataFactoryRunModel,
    DataFactoryWorkflowModel,
)
from services.api.parameter_models import (
    AutomationScriptParameterModel,
    ParameterEnumSetModel,
)
from services.api.project_models import ProjectModel
from services.api.quality_models import (
    RequirementModel,
    RequirementRevisionModel,
    TestCaseModel,
    TestCaseRevisionModel,
    TraceLinkModel,
)
from services.api.secret_grant_models import SecretCapabilityGrantModel
from services.api.test_plan_models import (
    ExecutionBatchModel,
    RunSpecModel,
    TestPlanModel,
    TestPlanRevisionModel,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
_registered_models = (
    UserModel, UserSessionModel, AuthBootstrapModel,
    RunModel, AttemptModel, EventModel, RequirementModel, RequirementRevisionModel,
    TestCaseModel, TestCaseRevisionModel, TraceLinkModel,
    AutomationScriptModel, AutomationScriptRevisionModel, TestCaseScriptLinkModel,
    EnvironmentModel, EnvironmentRevisionModel, EnvironmentValueModel,
    ParameterEnumSetModel, AutomationScriptParameterModel,
    ProjectModel,
    SecretCapabilityGrantModel,
    TestPlanModel, TestPlanRevisionModel, ExecutionBatchModel, RunSpecModel,
    DataFactoryWorkflowModel, DataFactoryRunModel,
)


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_database_engine(get_database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()