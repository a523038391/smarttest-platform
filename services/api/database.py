import os
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_database_engine(
    database_url: str | None = None,
    *,
    engine_options: Mapping[str, Any] | None = None,
) -> Engine:
    """Build an engine without opening a connection or creating schema objects."""
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")

    options = dict(engine_options or {})
    parsed_url = make_url(url)
    if parsed_url.get_backend_name() == "sqlite" and parsed_url.database in {
        None,
        "",
        ":memory:",
    }:
        connect_args = dict(options.pop("connect_args", {}))
        connect_args.setdefault("check_same_thread", False)
        options.setdefault("poolclass", StaticPool)
        options["connect_args"] = connect_args
    else:
        options.setdefault("pool_pre_ping", True)
    return create_engine(url, **options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)