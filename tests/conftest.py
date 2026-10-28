"""Shared fixtures. Tests run without Claude, the network or a real API key.

By default every test gets a fresh SQLite database. Set JENIUS_TEST_DATABASE_URL to a PostgreSQL URL
to run the same tests against Postgres (CI does both).
"""
import os

import pytest
from fastapi.testclient import TestClient

from jenius.api.app import create_app
from jenius.config import load_settings
from jenius.db import Base, Database

PG_URL = os.environ.get("JENIUS_TEST_DATABASE_URL", "")


@pytest.fixture
def make_settings(tmp_path):
    def make(**overrides):
        base = {"jenius_env": "test", "database_url": PG_URL or f"sqlite:///{tmp_path}/test.db",
                "public_url": "http://testserver", "llm_provider": "offline", "_env_file": None}
        return load_settings(**{**base, **overrides})
    return make


@pytest.fixture
def database(make_settings):
    db = Database(make_settings().database_url)
    if PG_URL:
        Base.metadata.drop_all(db.engine)
    db.create_all()
    yield db
    if PG_URL:
        Base.metadata.drop_all(db.engine)
    db.engine.dispose()


@pytest.fixture
def make_client(make_settings, database):
    clients = []

    def make(agent=None, **overrides):
        app = create_app(make_settings(**overrides), database, agent=agent)
        client = TestClient(app)
        client.__enter__()  # runs startup, which loads the demo dataset
        clients.append(client)
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()
