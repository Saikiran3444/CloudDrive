"""Database setup. SQLite is the zero-configuration local default."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

database_file = (
    Path("/tmp") / "clouddrive.db"
    if os.getenv("VERCEL")
    else Path(__file__).resolve().parent / "clouddrive.db"
)
DEFAULT_DATABASE_URL = f"sqlite:///{database_file.as_posix()}"
DATABASE_URL = next(
    (
        value.strip()
        for name in (
            "DATABASE_URL",
            "POSTGRES_URL",
            "POSTGRES_PRISMA_URL",
            "POSTGRES_URL_NON_POOLING",
            "POSTGRES_URL_NO_SSL",
        )
        if (value := os.getenv(name, "").strip())
    ),
    DEFAULT_DATABASE_URL,
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL.removeprefix("postgres://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL.removeprefix("postgresql://")
_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
    return _engine


class Base(DeclarativeBase):
    pass


def get_db():
    engine = get_engine()
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        Base.metadata.create_all(bind=get_engine())
        yield db
    finally:
        db.close()
