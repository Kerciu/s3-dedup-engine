"""Shared fixtures and test doubles for the AI worker suite."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import (
    Callable,
    ClassVar,
    Dict,
    Iterator,
    List,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import grpc
import numpy as np
import pytest
import torch
from PIL import Image

import db
from const.pg import STATUS_INSERTED
from const.sscd import EMBEDDING_DIMENSIONS
from db import DedupOutcome
from embeddings.sscd import SscdEmbedder
from pb import dedup_pb2
from quality.score import QualityScorer

JpegFactory = Callable[..., io.BytesIO]
NamedJpeg = Callable[[str], io.BytesIO]
EmbeddingFactory = Callable[[int], List[float]]
SqlParams = Union[Sequence[object], Mapping[str, object], None]
Row = Optional[Tuple[object, ...]]

IMAGE_VARIANTS: Dict[str, Tuple[Tuple[int, int], Optional[Tuple[int, int]]]] = {
    "uhd": ((3840, 2160), None),
    "hd": ((1920, 1080), None),
    "sd": ((640, 480), None),
    "tiny": ((160, 120), None),
    "hd_blurred": ((1920, 1080), (240, 135)),
}


class Aborted(Exception):
    """Raised by the fake servicer context in place of a real gRPC abort."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


@dataclass
class FakeServicerContext:
    """Records abort calls and raises so control flow matches the gRPC runtime."""

    aborts: List[Tuple[grpc.StatusCode, str]] = field(default_factory=list)

    def abort(self, code: grpc.StatusCode, details: str) -> NoReturn:
        """Records the abort and raises Aborted."""
        self.aborts.append((code, details))
        raise Aborted(code, details)


@dataclass
class Statement:
    """A SQL statement captured by the fake cursor."""

    sql: str
    params: SqlParams

    def mentions(self, needle: str) -> bool:
        """Returns True when the statement contains the needle, ignoring case."""
        return needle.lower() in self.sql.lower()

    def positional(self) -> Sequence[object]:
        """Returns the positional parameters, failing on mapping parameters."""
        if isinstance(self.params, (list, tuple)):
            return self.params
        raise AssertionError(f"statement did not use positional params: {self.sql}")


@dataclass
class FakeCursor:
    """Minimal psycopg cursor double that returns queued SELECT rows in order."""

    rows: List[Row] = field(default_factory=list)
    statements: List[Statement] = field(default_factory=list, init=False)
    _fetched: Row = field(default=None, init=False, repr=False)

    def execute(self, sql: str, params: SqlParams = None) -> FakeCursor:
        """Records the statement and pops the next queued row for SELECTs."""
        self.statements.append(Statement(sql, params))
        if sql.strip().upper().startswith("SELECT"):
            self._fetched = self.rows.pop(0) if self.rows else None
        return self

    def fetchone(self) -> Row:
        """Returns the row queued for the most recent SELECT."""
        return self._fetched

    def executed(self, needle: str) -> List[Statement]:
        """Returns every recorded statement containing the needle."""
        return [item for item in self.statements if item.mentions(needle)]

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeTransaction:
    """No-op stand-in for a psycopg transaction context."""

    def __enter__(self) -> FakeTransaction:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@dataclass
class FakeConnection:
    """Minimal psycopg connection double handing out one shared cursor."""

    shared_cursor: FakeCursor

    def cursor(self) -> FakeCursor:
        """Returns the shared fake cursor."""
        return self.shared_cursor

    def transaction(self) -> FakeTransaction:
        """Returns a no-op transaction context."""
        return FakeTransaction()

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@dataclass
class FakeScriptModule:
    """Stand-in for the SSCD TorchScript graph with a deterministic output."""

    OUTPUT_SCALE: ClassVar[float] = 7.0

    dimensions: int = EMBEDDING_DIMENSIONS
    input_shapes: List[Tuple[int, ...]] = field(default_factory=list, init=False)
    load_calls: int = field(default=0, init=False)

    def __call__(self, batch: torch.Tensor) -> torch.Tensor:
        """Records the input shape and returns an unnormalized descriptor."""
        self.input_shapes.append(tuple(int(dim) for dim in batch.shape))
        generator = torch.Generator().manual_seed(self.dimensions)
        output: torch.Tensor = torch.randn(1, self.dimensions, generator=generator)
        return output * self.OUTPUT_SCALE

    def eval(self) -> FakeScriptModule:
        """Mirrors the TorchScript eval call by returning self."""
        return self


@dataclass
class FakeRepository:
    """Repository double recording schema setup and replaying one outcome."""

    outcome: DedupOutcome = field(
        default_factory=lambda: DedupOutcome(STATUS_INSERTED, 1.0, "")
    )
    schema_calls: int = field(default=0, init=False)
    resolved: List[Tuple[str, int, float]] = field(default_factory=list, init=False)
    schema_error: Optional[Exception] = field(default=None, init=False)

    def init_schema(self) -> None:
        """Counts the call and raises the configured error, when present."""
        self.schema_calls += 1
        if self.schema_error is not None:
            raise self.schema_error

    def resolve(
        self,
        image_key: str,
        embedding: Sequence[float],
        quality_score: float,
    ) -> DedupOutcome:
        """Records the call and returns the configured outcome."""
        self.resolved.append((image_key, len(embedding), quality_score))
        return self.outcome


def _octave_noise(size: Tuple[int, int], seed: int) -> np.ndarray:
    """Builds deterministic multi-scale noise that degrades under downscaling."""
    width, height = size
    rng = np.random.default_rng(seed)
    accumulated = np.zeros((height, width), dtype=np.float32)
    amplitude = 1.0
    for octave in range(1, 8):
        cells = 2**octave
        coarse = (rng.random((cells, cells)) * 255).astype(np.uint8)
        layer = Image.fromarray(coarse).resize(
            (width, height), Image.Resampling.BICUBIC
        )
        accumulated += amplitude * np.asarray(layer, dtype=np.float32)
        amplitude *= 0.5
    accumulated -= float(accumulated.min())
    accumulated /= max(float(accumulated.max()), 1e-6)
    return (accumulated * 255.0).astype(np.uint8)


@pytest.fixture(scope="session")
def master_image() -> Image.Image:
    """A deterministic 4K source image carrying natural multi-scale detail."""
    gray = _octave_noise((3840, 2160), seed=11)
    return Image.fromarray(np.dstack([gray, gray, gray]))


@pytest.fixture(scope="session")
def jpeg_factory(master_image: Image.Image) -> JpegFactory:
    """Returns a factory encoding the master image at an arbitrary size."""

    def factory(
        size: Tuple[int, int],
        quality: int = 92,
        degrade_via: Optional[Tuple[int, int]] = None,
    ) -> io.BytesIO:
        image = master_image
        if degrade_via is not None:
            image = image.resize(degrade_via, Image.Resampling.LANCZOS)
        image = image.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        buffer.seek(0)
        return buffer

    return factory


@pytest.fixture(scope="session")
def encoded_images(jpeg_factory: JpegFactory) -> Dict[str, bytes]:
    """Encodes the shared image variants once per session."""
    return {
        name: jpeg_factory(size, degrade_via=degrade).getvalue()
        for name, (size, degrade) in IMAGE_VARIANTS.items()
    }


@pytest.fixture
def jpeg(encoded_images: Dict[str, bytes]) -> NamedJpeg:
    """Returns a factory handing out a fresh buffer for a named image variant."""

    def factory(name: str) -> io.BytesIO:
        return io.BytesIO(encoded_images[name])

    return factory


@pytest.fixture
def scorer() -> QualityScorer:
    """A quality scorer instance."""
    return QualityScorer()


@pytest.fixture
def embedding_factory() -> EmbeddingFactory:
    """Returns a factory producing deterministic unit-norm embeddings."""

    def factory(seed: int) -> List[float]:
        rng = np.random.default_rng(seed)
        vector = rng.normal(size=EMBEDDING_DIMENSIONS)
        vector /= np.linalg.norm(vector)
        return [float(value) for value in vector]

    return factory


@pytest.fixture
def unit_embedding(embedding_factory: EmbeddingFactory) -> List[float]:
    """A single deterministic unit-norm embedding."""
    return embedding_factory(1)


@pytest.fixture
def fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> Tuple[SscdEmbedder, FakeScriptModule]:
    """An embedder whose TorchScript graph is replaced by a fake module."""
    model = FakeScriptModule()

    def load_model(_self: SscdEmbedder) -> FakeScriptModule:
        model.load_calls += 1
        return model.eval()

    monkeypatch.setattr(SscdEmbedder, "_load_model", load_model)
    return SscdEmbedder(), model


@pytest.fixture
def fake_repository() -> FakeRepository:
    """A repository double that avoids touching PostgreSQL."""
    return FakeRepository()


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeCursor]:
    """Patches psycopg.connect and returns a helper that queues SELECT rows."""

    def install(rows: Optional[List[Row]] = None) -> FakeCursor:
        cursor = FakeCursor(list(rows or []))
        monkeypatch.setattr(
            db.psycopg, "connect", lambda *_args, **_kwargs: FakeConnection(cursor)
        )
        return cursor

    return install


@pytest.fixture
def chunk_factory() -> Callable[..., List[dedup_pb2.ImageChunk]]:
    """Returns a factory splitting bytes into real ImageChunk messages."""

    def factory(
        payload: bytes,
        image_key: str = "image.jpg",
        chunk_size: int = 1024,
    ) -> List[dedup_pb2.ImageChunk]:
        chunks: List[dedup_pb2.ImageChunk] = []
        for offset in range(0, max(len(payload), 1), chunk_size):
            first = offset == 0
            chunks.append(
                dedup_pb2.ImageChunk(
                    image_key=image_key if first else "",
                    total_size=len(payload) if first else 0,
                    data=payload[offset : offset + chunk_size],
                )
            )
        return chunks

    return factory


@pytest.fixture
def grpc_context() -> FakeServicerContext:
    """A servicer context double that records aborts."""
    return FakeServicerContext()


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Restores the root logger configuration after a test mutates it."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
