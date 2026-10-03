"""Tests for the PyPika-rendered pgvector statements."""

from __future__ import annotations

from typing import List

import pytest

from const.pg import (
    PG_EMBEDDINGS_TABLE,
    PG_HNSW_INDEX,
    PG_HNSW_METHOD,
    PG_VECTOR_COSINE_OPS,
    PG_VECTOR_EXTENSION,
)
from const.sscd import EMBEDDING_DIMENSIONS
from sql import (
    EMBEDDINGS,
    ClosestStoredEmbeddingQuery,
    CosineDistance,
    CreateEmbeddingsTableQuery,
    CreateHnswIndexQuery,
    CreateVectorExtensionQuery,
    HnswIndexBuilder,
    LockQualityScoreQuery,
    PromoteEmbeddingQuery,
    SQLQuery,
    UpsertEmbeddingQuery,
)

RUNTIME_QUERIES: List[SQLQuery] = [
    ClosestStoredEmbeddingQuery(),
    LockQualityScoreQuery(),
    UpsertEmbeddingQuery(),
    PromoteEmbeddingQuery(),
]


@pytest.mark.parametrize("query", RUNTIME_QUERIES)
def test_runtime_statements_bind_values_instead_of_inlining_them(
    query: SQLQuery,
) -> None:
    assert "%" in query.sql
    assert "'" not in query.sql


@pytest.mark.parametrize("query", RUNTIME_QUERIES)
def test_runtime_statements_target_the_embeddings_table(query: SQLQuery) -> None:
    assert PG_EMBEDDINGS_TABLE in query.sql


def test_sql_query_is_abstract() -> None:
    with pytest.raises(TypeError):
        SQLQuery()  # type: ignore[abstract]


def test_cosine_distance_renders_the_pgvector_operator() -> None:
    assert str(CosineDistance().term()) == '"embedding" <=> %(vector)s::vector'


def test_nearest_lookup_orders_by_cosine_distance_and_takes_one_row() -> None:
    statement = ClosestStoredEmbeddingQuery().sql
    assert statement.count(str(CosineDistance().term())) == 2
    assert "ORDER BY" in statement
    assert statement.endswith("LIMIT 1")


def test_nearest_lookup_selects_the_neighbor_columns_in_row_order() -> None:
    selected = ClosestStoredEmbeddingQuery().sql.split(" FROM ")[0]
    assert selected.index('"id"') < selected.index('"image_key"')
    assert selected.index('"image_key"') < selected.index('"quality_score"')
    assert selected.index('"quality_score"') < selected.index("<=>")


def test_quality_score_is_read_under_a_row_lock() -> None:
    statement = LockQualityScoreQuery().sql
    assert statement.endswith("FOR UPDATE")
    assert statement.count("%s") == 1


def test_upsert_resolves_a_key_collision_from_the_excluded_row() -> None:
    statement = UpsertEmbeddingQuery().sql
    assert 'ON CONFLICT ("image_key") DO UPDATE' in statement
    assert '"embedding"=EXCLUDED."embedding"' in statement
    assert '"quality_score"=EXCLUDED."quality_score"' in statement
    assert statement.count("%s") == 3
    assert "%s::vector" in statement


def test_promotion_updates_the_incumbent_row_by_id() -> None:
    statement = PromoteEmbeddingQuery().sql
    assert statement.startswith("UPDATE")
    assert statement.endswith('WHERE "id"=%s')
    assert statement.count("%s") == 4
    assert "%s::vector" in statement


def test_table_declares_the_vector_dimension_and_a_unique_image_key() -> None:
    statement = CreateEmbeddingsTableQuery().sql
    assert "CREATE TABLE IF NOT EXISTS" in statement
    assert f"VECTOR({EMBEDDING_DIMENSIONS})" in statement
    assert 'UNIQUE ("image_key")' in statement
    assert 'PRIMARY KEY ("id")' in statement
    assert "DEFAULT NOW()" in statement


def test_hnsw_index_uses_the_cosine_operator_class() -> None:
    statement = CreateHnswIndexQuery().sql
    assert PG_HNSW_INDEX in statement
    assert f"USING {PG_HNSW_METHOD} (embedding {PG_VECTOR_COSINE_OPS})" in statement


def test_hnsw_index_is_built_through_pypika() -> None:
    statement = CreateHnswIndexQuery().sql
    rebuilt = str(
        HnswIndexBuilder.hnsw(
            PG_HNSW_INDEX,
            EMBEDDINGS,
            "embedding",
            PG_HNSW_METHOD,
            PG_VECTOR_COSINE_OPS,
        )
    )
    assert statement == rebuilt


def test_vector_extension_names_pgvector() -> None:
    assert PG_VECTOR_EXTENSION in CreateVectorExtensionQuery().sql
