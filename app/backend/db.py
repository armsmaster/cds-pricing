from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _default_url() -> str:
    configured = os.environ.get("PCDS_DB_PATH")
    if configured:
        return f"sqlite:///{configured}"
    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'app.db').as_posix()}"


def create_session_factory(url: str) -> sessionmaker[Session]:
    """Build an engine at ``url``, create all tables, return a session factory."""
    engine = create_engine(url, connect_args={"check_same_thread": False})
    from app.backend import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)
    _ensure_columns(
        engine,
        "market_data",
        {"override_clean": "FLOAT", "included": "BOOLEAN DEFAULT 1"},
    )
    _ensure_columns(
        engine,
        "rate_curves",
        {"updated_at": "TIMESTAMP"},
    )
    # Seed reference data from bundled JSON on first startup (tables empty).
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed_session:
        from app.backend import data_service

        data_service.seed_from_json(seed_session)
    return factory


def _ensure_columns(engine: Engine, table: str, columns: dict[str, str]) -> None:
    """Add missing columns to an existing table (lightweight, no Alembic)."""
    existing = {col["name"] for col in inspect(engine).get_columns(table)}
    with engine.begin() as conn:
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


_session_factory: sessionmaker[Session] | None = None


def configure(url: str | None = None) -> None:
    global _session_factory
    _session_factory = create_session_factory(url or _default_url())


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session (configures the default DB lazily)."""
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    with _session_factory() as session:
        yield session
