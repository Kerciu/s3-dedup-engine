"""pgvector deduplication statements rendered with PyPika."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, cast

from pypika import Column, Parameter, PostgreSQLQuery, Query, Table
from pypika.dialects import PostgreSQLQueryBuilder
from pypika.enums import Comparator
from pypika.queries import CreateIndexBuilder
from pypika.terms import BasicCriterion, Function, Term
from pypika.utils import builder

from const.pg import (
    PG_EMBEDDINGS_TABLE,
    PG_HNSW_INDEX,
    PG_HNSW_METHOD,
    PG_VECTOR_COSINE_OPS,
    PG_VECTOR_EXTENSION,
)
from const.sscd import EMBEDDING_DIMENSIONS

EMBEDDINGS = Table(PG_EMBEDDINGS_TABLE)


class VectorOperator(Comparator):
    """pgvector distance operators usable as PyPika criteria."""

    cosine_distance = " <=> "


class HnswIndexBuilder(CreateIndexBuilder):
    """CREATE INDEX builder that emits USING and an operator class."""

    def __init__(self) -> None:
        super().__init__()
        self._using: Optional[str] = None
        self._ops: Optional[str] = None

    @builder
    def using(self, method: str) -> None:
        self._using = method

    @builder
    def ops(self, operator_class: str) -> None:
        self._ops = operator_class

    def get_sql(self) -> str:
        """Renders CREATE INDEX with an optional access method and opclass."""
        if not self._columns:
            raise AttributeError("Cannot create index without columns")
        if self._table is None:
            raise AttributeError("Cannot create index without table")
        columns = ", ".join(self._column_sql(column) for column in self._columns)
        exists = "IF NOT EXISTS " if self._if_not_exists else ""
        using = f" USING {self._using} " if self._using else " "
        return f"CREATE INDEX {exists}{self._index} ON {self._table}{using}({columns})"

    def _column_sql(self, column: Column) -> str:
        """Formats one indexed column, appending the operator class when set."""
        name = column.name
        if self._ops is None:
            return name
        return f"{name} {self._ops}"

    @classmethod
    def hnsw(
        cls,
        name: str,
        table: Table,
        column: str,
        method: str,
        operator_class: str,
    ) -> HnswIndexBuilder:
        """Builds an IF NOT EXISTS HNSW index with the given operator class."""
        index = cast(
            HnswIndexBuilder,
            cls().create_index(name).on(table).columns(column).if_not_exists(),
        )
        return index.using(method).ops(operator_class)


@dataclass(frozen=True)
class SQLQuery(ABC):
    """PyPika-rendered statement with bound-parameter placeholders."""

    @abstractmethod
    def build(self) -> str:
        """Returns the statement with placeholders, never inlined values."""

    @property
    def sql(self) -> str:
        """Returns the rendered statement."""
        return self.build()

    def __str__(self) -> str:
        return self.sql


@dataclass(frozen=True)
class CosineDistance:
    """pgvector cosine distance between the stored and bound vectors."""

    left: Term = field(default=EMBEDDINGS.embedding)
    right: Term = field(default_factory=lambda: Parameter("%(vector)s::vector"))

    def term(self) -> Term:
        """Returns the distance as a PyPika criterion."""
        return BasicCriterion(VectorOperator.cosine_distance, self.left, self.right)


@dataclass(frozen=True)
class ClosestStoredEmbeddingQuery(SQLQuery):
    """Nearest stored embedding ordered by cosine distance."""

    def build(self) -> str:
        distance = CosineDistance().term()
        return str(
            PostgreSQLQuery.from_(EMBEDDINGS)
            .select(
                EMBEDDINGS.id,
                EMBEDDINGS.image_key,
                EMBEDDINGS.quality_score,
                distance,
            )
            .orderby(distance)
            .limit(1)
        )


@dataclass(frozen=True)
class LockQualityScoreQuery(SQLQuery):
    """Locking re-read of the incumbent quality score."""

    def build(self) -> str:
        return str(
            PostgreSQLQuery.from_(EMBEDDINGS)
            .select(EMBEDDINGS.quality_score)
            .where(EMBEDDINGS.id == Parameter("%s"))
            .for_update()
        )


@dataclass(frozen=True)
class UpsertEmbeddingQuery(SQLQuery):
    """Insert that overwrites any row holding the same image key."""

    def build(self) -> str:
        insert = cast(
            PostgreSQLQueryBuilder,
            PostgreSQLQuery.into(EMBEDDINGS)
            .columns(
                EMBEDDINGS.image_key, EMBEDDINGS.embedding, EMBEDDINGS.quality_score
            )
            .insert(Parameter("%s"), Parameter("%s::vector"), Parameter("%s")),
        )
        return str(
            insert.on_conflict(EMBEDDINGS.image_key)
            .do_update(EMBEDDINGS.embedding)
            .do_update(EMBEDDINGS.quality_score)
        )


@dataclass(frozen=True)
class PromoteEmbeddingQuery(SQLQuery):
    """In-place promotion of a higher quality candidate."""

    def build(self) -> str:
        return str(
            PostgreSQLQuery.update(EMBEDDINGS)
            .set(EMBEDDINGS.image_key, Parameter("%s"))
            .set(EMBEDDINGS.embedding, Parameter("%s::vector"))
            .set(EMBEDDINGS.quality_score, Parameter("%s"))
            .where(EMBEDDINGS.id == Parameter("%s"))
        )


@dataclass(frozen=True)
class CreateVectorExtensionQuery(SQLQuery):
    """Enables the pgvector extension when it is not already present."""

    def build(self) -> str:
        return f"CREATE EXTENSION IF NOT EXISTS {PG_VECTOR_EXTENSION}"


@dataclass(frozen=True)
class CreateEmbeddingsTableQuery(SQLQuery):
    """Embeddings table with a vector column and a unique image key."""

    def build(self) -> str:
        return str(
            Query.create_table(EMBEDDINGS)
            .if_not_exists()
            .columns(
                Column("id", "BIGSERIAL", nullable=False),
                Column("image_key", "TEXT", nullable=False),
                Column("embedding", f"VECTOR({EMBEDDING_DIMENSIONS})", nullable=False),
                Column("quality_score", "DOUBLE PRECISION", nullable=False),
                Column(
                    "created_at",
                    "TIMESTAMPTZ",
                    nullable=False,
                    default=Function("NOW"),
                ),
            )
            .primary_key("id")
            .unique("image_key")
        )


@dataclass(frozen=True)
class CreateHnswIndexQuery(SQLQuery):
    """HNSW index on the embedding column using cosine operator class."""

    def build(self) -> str:
        return str(
            HnswIndexBuilder.hnsw(
                PG_HNSW_INDEX,
                EMBEDDINGS,
                "embedding",
                PG_HNSW_METHOD,
                PG_VECTOR_COSINE_OPS,
            )
        )
