"""Tests for the pgvector insert, replace and reject decision."""

from __future__ import annotations

from typing import Callable, List

import pytest

from const.pg import (
    MAX_COSINE_DISTANCE,
    NO_NEIGHBOR_DISTANCE,
    PG_DSN_DEFAULT,
    PG_DSN_ENV,
    STATUS_DUPLICATE_REJECTED,
    STATUS_INSERTED,
    STATUS_REPLACED,
)
from const.sscd import EMBEDDING_DIMENSIONS
from db import EmbeddingRepository, _to_vector_literal, resolve_dsn
from sql import (
    ClosestStoredEmbeddingQuery,
    CreateEmbeddingsTableQuery,
    CreateHnswIndexQuery,
    CreateVectorExtensionQuery,
    LockQualityScoreQuery,
    PromoteEmbeddingQuery,
    UpsertEmbeddingQuery,
)
from tests.conftest import FakeCursor, Row

InstallDb = Callable[..., FakeCursor]
NEAR = MAX_COSINE_DISTANCE / 2
FAR = MAX_COSINE_DISTANCE * 5


def test_resolve_dsn_prefers_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PG_DSN_ENV, "postgresql://user:pass@db:5432/other")
    assert resolve_dsn() == "postgresql://user:pass@db:5432/other"


def test_resolve_dsn_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PG_DSN_ENV, raising=False)
    assert resolve_dsn() == PG_DSN_DEFAULT


def test_repository_honours_an_explicit_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PG_DSN_ENV, "postgresql://ignored/db")
    repository = EmbeddingRepository(dsn="postgresql://explicit/db")
    assert repository.dsn == "postgresql://explicit/db"


def test_vector_literal_formats_a_bracketed_list() -> None:
    literal = _to_vector_literal([0.5] * EMBEDDING_DIMENSIONS)
    assert literal.startswith("[0.50000000,")
    assert literal.endswith("]")
    assert literal.count(",") == EMBEDDING_DIMENSIONS - 1


def test_vector_literal_rejects_the_wrong_dimension_count() -> None:
    with pytest.raises(ValueError, match=str(EMBEDDING_DIMENSIONS)):
        _to_vector_literal([0.1, 0.2])


def test_init_schema_creates_extension_table_and_hnsw_index(
    fake_db: InstallDb,
) -> None:
    cursor = fake_db()
    EmbeddingRepository(dsn="postgresql://fake/db").init_schema()
    assert cursor.executed(CreateVectorExtensionQuery().sql)
    assert cursor.executed(CreateEmbeddingsTableQuery().sql)
    assert cursor.executed(CreateHnswIndexQuery().sql)


def test_empty_table_inserts_with_the_no_neighbor_distance(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    cursor = fake_db([])
    outcome = EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "new.jpg", unit_embedding, 0.5
    )
    assert outcome.status == STATUS_INSERTED
    assert outcome.distance == NO_NEIGHBOR_DISTANCE
    assert outcome.existing_image_key == ""
    assert cursor.executed(UpsertEmbeddingQuery().sql)


def test_distant_neighbor_inserts_and_reports_its_distance(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(1, "other.jpg", 0.9, FAR)]
    cursor = fake_db(rows)
    outcome = EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "new.jpg", unit_embedding, 0.1
    )
    assert outcome.status == STATUS_INSERTED
    assert outcome.distance == pytest.approx(FAR)
    assert cursor.executed(UpsertEmbeddingQuery().sql)
    assert not cursor.executed(LockQualityScoreQuery().sql)


@pytest.mark.parametrize("candidate_quality", [0.3, 0.5])
def test_near_neighbor_with_equal_or_lower_quality_is_rejected(
    fake_db: InstallDb, unit_embedding: List[float], candidate_quality: float
) -> None:
    rows: List[Row] = [(7, "incumbent.jpg", 0.5, NEAR), (0.5,)]
    cursor = fake_db(rows)
    outcome = EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "candidate.jpg", unit_embedding, candidate_quality
    )
    assert outcome.status == STATUS_DUPLICATE_REJECTED
    assert outcome.existing_image_key == "incumbent.jpg"
    assert outcome.distance == pytest.approx(NEAR)
    assert cursor.executed(LockQualityScoreQuery().sql)
    assert not cursor.executed(UpsertEmbeddingQuery().sql)
    assert not cursor.executed(PromoteEmbeddingQuery().sql)


def test_near_neighbor_with_higher_quality_replaces_in_place(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(7, "incumbent.jpg", 0.4, NEAR), (0.4,)]
    cursor = fake_db(rows)
    outcome = EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "better.jpg", unit_embedding, 0.95
    )
    assert outcome.status == STATUS_REPLACED
    assert outcome.existing_image_key == "incumbent.jpg"
    updates = cursor.executed(PromoteEmbeddingQuery().sql)
    assert updates
    params = updates[0].positional()
    assert params[0] == "better.jpg"
    assert params[3] == 7


def test_replace_locks_the_incumbent_before_updating(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(7, "incumbent.jpg", 0.4, NEAR), (0.4,)]
    cursor = fake_db(rows)
    EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "better.jpg", unit_embedding, 0.95
    )
    executed = [statement.sql for statement in cursor.statements]
    assert executed.index(LockQualityScoreQuery().sql) < executed.index(
        PromoteEmbeddingQuery().sql
    )


def test_lock_uses_the_quality_score_read_under_the_lock(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(7, "incumbent.jpg", 0.1, NEAR), (0.99,)]
    fake_db(rows)
    outcome = EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "candidate.jpg", unit_embedding, 0.5
    )
    assert outcome.status == STATUS_DUPLICATE_REJECTED


def test_vanished_incumbent_raises(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(7, "incumbent.jpg", 0.4, NEAR)]
    fake_db(rows)
    with pytest.raises(RuntimeError, match="vanished"):
        EmbeddingRepository(dsn="postgresql://fake/db").resolve(
            "candidate.jpg", unit_embedding, 0.5
        )


def test_max_distance_is_configurable(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    rows: List[Row] = [(1, "other.jpg", 0.9, 0.4), (0.9,)]
    fake_db(rows)
    repository = EmbeddingRepository(dsn="postgresql://fake/db", max_distance=0.5)
    outcome = repository.resolve("candidate.jpg", unit_embedding, 0.1)
    assert outcome.status == STATUS_DUPLICATE_REJECTED


def test_nearest_query_orders_by_cosine_distance(
    fake_db: InstallDb, unit_embedding: List[float]
) -> None:
    cursor = fake_db([])
    EmbeddingRepository(dsn="postgresql://fake/db").resolve(
        "new.jpg", unit_embedding, 0.5
    )
    assert cursor.executed(ClosestStoredEmbeddingQuery().sql)
