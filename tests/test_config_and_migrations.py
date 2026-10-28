from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from jenius.config import load_settings
from jenius.db import Base, make_engine

ROOT = Path(__file__).resolve().parent.parent


def settings(**kw):
    return load_settings(_env_file=None, **kw)


def test_hosting_urls_are_normalised():
    assert settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert settings(database_url="postgresql://u:p@h/db").is_postgres


def test_production_is_strict():
    with pytest.raises(SystemExit, match="must be PostgreSQL in production"):
        settings(jenius_env="production", public_url="https://jenius.example.com")
    with pytest.raises(SystemExit, match="must start with https"):
        settings(jenius_env="production", database_url="postgresql://h/db", public_url="http://jenius.example.com")
    ok = settings(jenius_env="production", database_url="postgresql://h/db", public_url="https://jenius.example.com")
    assert ok.secure_cookies and ok.origins == {"https://jenius.example.com"}


def test_claude_needs_a_key():
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is required"):
        settings(llm_provider="anthropic")


def test_migrations_match_the_models(tmp_path):
    url = f"sqlite:///{tmp_path}/migrated.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
