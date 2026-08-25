"""Fixtures for the Postgres-backed integration tier.

This tier exists because the unit suite never opens a database connection, and everything
architecturally interesting about Groundwork is SQL: the RRF fusion query, the content-hash
upsert, the sync state machine, and the citation snapshot. `tests/test_retrieval.py` compiles the
retrieval statement and greps the string; it cannot tell you whether the query returns the right
rows.

Opt-in by design. Without `TEST_DATABASE_URL` the whole package skips, so the default `pytest` run
stays hermetic and fast. CI attaches this tier to the existing `schema` job, which already stands
up pgvector and applies migrations.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.db.models import Project

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.skip(
        "Set TEST_DATABASE_URL to run the Postgres-backed integration tier.",
        allow_module_level=True,
    )

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _split_database_url(url: str) -> tuple[str, str]:
    """Split a URL into (server URL without database, database name)."""
    base, _, database = url.rpartition("/")
    return base, database


async def _create_database_if_missing(url: str) -> None:
    """Create the test database if it does not exist yet.

    `CREATE DATABASE` cannot run inside a transaction, and it must be issued from a *different*
    database, so this connects to the `postgres` maintenance database first.
    """
    server, database = _split_database_url(url)
    admin_engine = create_async_engine(
        f"{server}/postgres", poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin_engine.connect() as connection:
            exists = await connection.scalar(
                sa_text("select 1 from pg_database where datname = :name"), {"name": database}
            )
            if not exists:
                await connection.execute(sa_text(f'create database "{database}"'))
    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> str:
    """Build the schema by running the shipped migrations, once per session.

    Deliberately not `Base.metadata.create_all`. The `vector` extension and the partial HNSW index
    are raw SQL inside the revisions, so only `alembic upgrade head` proves that the schema this
    project actually ships produces a working database.

    Alembic runs in a subprocess because `alembic/env.py` ends in `asyncio.run(...)`, which raises
    if called while pytest-asyncio already has a loop running. The subprocess also means the
    migration reads `DATABASE_URL` through the ordinary settings path rather than a patched object.
    """
    asyncio.run(_create_database_if_missing(TEST_DATABASE_URL))

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session")
async def engine(migrated_database: str):
    # NullPool keeps connections from outliving the per-test event loop, which asyncpg dislikes.
    created = create_async_engine(migrated_database, poolclass=NullPool)
    yield created
    await created.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(engine) -> None:
    """Truncate every table before each test.

    The first attempt here bound each session to an outer transaction with
    `join_transaction_mode="create_savepoint"` and rolled it back at teardown. That works for code
    that never manages its own transaction, and these services do: `ingest_documents` and every
    `sync_*_project` call `session.commit()`, and the sync failure path calls `session.rollback()`.
    That rollback unwinds the savepoint the fixture is relying on, after which a later commit
    reaches the real transaction and the test's rows are committed for good — data leaked between
    tests and the failures pointed at the wrong code.

    Truncation makes isolation independent of what the code under test does with its transaction,
    which is the right trade when the transaction boundary is genuinely owned by the service. It
    runs *before* each test rather than after, so a crashed run leaves its rows behind to inspect.
    """
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(sa_text(f"truncate {tables} restart identity cascade"))


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(bind=engine, expire_on_commit=False) as test_session:
        yield test_session


@pytest_asyncio.fixture
async def project(session: AsyncSession) -> Project:
    record = Project(
        id="test-project",
        name="Test Project",
        repo="acme/test",
        jira_project_key="TEST",
        slack_channel_ids=["C1"],
        status="Active",
        health="green",
    )
    session.add(record)
    await session.flush()
    return record


@pytest_asyncio.fixture
async def other_project(session: AsyncSession) -> Project:
    """A second project, so scoping assertions have something to leak into."""
    record = Project(
        id="other-project",
        name="Other Project",
        repo="acme/other",
        status="Active",
        health="green",
    )
    session.add(record)
    await session.flush()
    return record


# --- Embeddings -------------------------------------------------------------------------------
#
# Never Ollama. These tests assert SQL behaviour, and a real model would make cosine ordering
# approximate and the suite slow and non-hermetic. Model quality is the eval harnesses' job.


def unit_vector(*weights: float) -> list[float]:
    """A normalized embedding whose leading components are `weights`.

    Writing vectors by hand makes the expected cosine ordering exact and legible, so a ranking
    assertion states a fact rather than a hope.
    """
    vector = [0.0] * settings.embedding_dimension
    for index, weight in enumerate(weights):
        vector[index] = float(weight)
    magnitude = sum(value * value for value in vector) ** 0.5
    if magnitude == 0:
        raise ValueError("A zero vector has no direction and cosine distance is undefined for it.")
    return [value / magnitude for value in vector]


def _deterministic_vector(text: str) -> list[float]:
    digest = sha256(text.encode("utf-8")).digest()
    raw = [digest[index % len(digest)] / 255.0 for index in range(settings.embedding_dimension)]
    magnitude = sum(value * value for value in raw) ** 0.5
    return [value / magnitude for value in raw]


class StubEmbedder:
    """Stands in for `OllamaClient`, matching only the `embed` method the callers use.

    Texts registered in `vectors` get exactly the vector given; anything else gets a deterministic
    pseudo-random one, so unregistered content is stable across runs but never accidentally close
    to a registered vector.
    """

    def __init__(self, vectors: dict[str, list[float]] | None = None) -> None:
        self.vectors = vectors or {}
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text) or _deterministic_vector(text) for text in texts]


class FailingEmbedder:
    """Models Ollama being down, which retrieval and ingestion both claim to degrade around."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("Ollama is unavailable")
