"""pgvector-backed repository resolving image deduplication decisions atomically."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

import psycopg
from psycopg import Cursor
from psycopg.rows import TupleRow

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
from logger import get_logger
from sql import (
    ClosestStoredEmbeddingQuery,
    CreateEmbeddingsTableQuery,
    CreateHnswIndexQuery,
    CreateVectorExtensionQuery,
    LockQualityScoreQuery,
    PromoteEmbeddingQuery,
    UpsertEmbeddingQuery,
)

log = get_logger()


def resolve_dsn() -> str:
    """Returns the configured PostgreSQL DSN or the local development default."""
    dsn = os.getenv(PG_DSN_ENV)
    return dsn if dsn else PG_DSN_DEFAULT


@dataclass(frozen=True)
class DedupOutcome:
    """Decision taken for an embedding against its nearest stored neighbor."""

    status: str
    distance: float
    existing_image_key: str


@dataclass(frozen=True)
class Neighbor:
    """Nearest stored embedding row and its cosine distance to the candidate."""

    row_id: int
    image_key: str
    quality_score: float
    distance: float


@dataclass(frozen=True)
class EmbeddingRepository:
    """Owns the embeddings schema and the insert, replace or reject decision."""

    dsn: str = field(default_factory=resolve_dsn)
    max_distance: float = MAX_COSINE_DISTANCE

    def init_schema(self) -> None:
        """Creates the vector extension, embeddings table and HNSW index if absent."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(CreateVectorExtensionQuery().sql)
            cur.execute(CreateEmbeddingsTableQuery().sql)
            cur.execute(CreateHnswIndexQuery().sql)

    def resolve(
        self,
        image_key: str,
        embedding: Sequence[float],
        quality_score: float,
    ) -> DedupOutcome:
        """Inserts, replaces or rejects the candidate inside a single transaction."""
        vector = _to_vector_literal(embedding)
        with psycopg.connect(self.dsn) as conn:
            with conn.transaction(), conn.cursor() as cur:
                neighbor = self._nearest(cur, vector)
                log.debug(
                    "nearest neighbor for %s: %s",
                    image_key,
                    neighbor or "none",
                )
                if neighbor is None or neighbor.distance > self.max_distance:
                    self._insert(cur, image_key, vector, quality_score)
                    distance = (
                        NO_NEIGHBOR_DISTANCE if neighbor is None else neighbor.distance
                    )
                    return DedupOutcome(STATUS_INSERTED, distance, "")

                incumbent_quality = self._lock_quality(cur, neighbor.row_id)
                if quality_score <= incumbent_quality:
                    return DedupOutcome(
                        STATUS_DUPLICATE_REJECTED,
                        neighbor.distance,
                        neighbor.image_key,
                    )

                self._replace(cur, neighbor.row_id, image_key, vector, quality_score)
                return DedupOutcome(
                    STATUS_REPLACED,
                    neighbor.distance,
                    neighbor.image_key,
                )

    @staticmethod
    def _nearest(cur: Cursor[TupleRow], vector: str) -> Optional[Neighbor]:
        """Returns the closest stored embedding by cosine distance, if any exists."""
        cur.execute(ClosestStoredEmbeddingQuery().sql, {"vector": vector})
        row = cur.fetchone()
        if row is None:
            return None
        return Neighbor(
            row_id=int(row[0]),
            image_key=str(row[1]),
            quality_score=float(row[2]),
            distance=float(row[3]),
        )

    @staticmethod
    def _lock_quality(cur: Cursor[TupleRow], row_id: int) -> float:
        """Locks the incumbent row and re-reads its authoritative quality score."""
        cur.execute(LockQualityScoreQuery().sql, (row_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"embedding row {row_id} vanished mid-transaction")
        return float(row[0])

    @staticmethod
    def _insert(
        cur: Cursor[TupleRow],
        image_key: str,
        vector: str,
        quality_score: float,
    ) -> None:
        """Stores a new embedding, overwriting any row with the same image key."""
        cur.execute(
            UpsertEmbeddingQuery().sql,
            (image_key, vector, quality_score),
        )

    @staticmethod
    def _replace(
        cur: Cursor[TupleRow],
        row_id: int,
        image_key: str,
        vector: str,
        quality_score: float,
    ) -> None:
        """Promotes the higher quality candidate in place of the incumbent row."""
        cur.execute(
            PromoteEmbeddingQuery().sql,
            (image_key, vector, quality_score, row_id),
        )


def _to_vector_literal(embedding: Sequence[float]) -> str:
    """Formats an embedding as a pgvector bracketed literal."""
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"expected {EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}"
        )
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
