"""PostgreSQL, pgvector and deduplication-status constants."""

from typing import Final

PG_DSN_ENV: Final[str] = "PG_DSN"
PG_DSN_DEFAULT: Final[str] = "postgresql://dedup:dedup@localhost:5432/dedup"
PG_EMBEDDINGS_TABLE: Final[str] = "image_embeddings"
PG_VECTOR_EXTENSION: Final[str] = "vector"
PG_HNSW_INDEX: Final[str] = "image_embeddings_embedding_hnsw"
PG_HNSW_METHOD: Final[str] = "hnsw"
PG_VECTOR_COSINE_OPS: Final[str] = "vector_cosine_ops"
NO_NEIGHBOR_DISTANCE: Final[float] = 1.0
PG_RESOLVE_LOCK_ID: Final[int] = 74821001

STATUS_INSERTED: Final[str] = "inserted"
STATUS_REPLACED: Final[str] = "replaced"
STATUS_DUPLICATE_REJECTED: Final[str] = "duplicate_rejected"
