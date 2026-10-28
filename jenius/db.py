"""The database connection. PostgreSQL in production, a SQLite file on a laptop and in tests."""
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything Jenius stores is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:" and not path.startswith("file:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")  # SQLite ignores ON DELETE CASCADE without this
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    # pool_pre_ping drops connections the database closed while idle (common on managed Postgres).
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800)


class Database:
    """One engine and session factory per app, so tests can each use their own database."""

    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Iterator[Session]:
        with self.sessions() as db:
            yield db

    def create_all(self) -> None:
        """Tests and quick local runs only; real databases are set up with `alembic upgrade head`."""
        from . import models  # noqa: F401  (registers the tables)

        Base.metadata.create_all(self.engine)
