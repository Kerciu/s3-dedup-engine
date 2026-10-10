"""gRPC worker streaming image chunks into SSCD embedding and pgvector dedup."""

from __future__ import annotations

import signal
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from types import FrameType, TracebackType
from typing import IO, ClassVar, Iterator, Optional, Sequence, Tuple, Type

import grpc

from const.grpc import (
    GRPC_MAX_MESSAGE_BYTES,
    GRPC_MAX_WORKERS,
    GRPC_PORT,
    GRPC_SHUTDOWN_GRACE_SECONDS,
    SPOOL_MAX_BYTES,
)
from db import EmbeddingRepository
from embeddings.sscd import SscdEmbedder, get_embedder
from logger import get_logger, setup_logging
from pb import dedup_pb2, dedup_pb2_grpc
from quality.score import QualityScorer, get_scorer

log = get_logger()

ServerOptions = Sequence[Tuple[str, int]]


class SpooledImage:
    """Collects streamed chunks in memory and spills to disk past a size limit."""

    def __init__(self, max_memory_bytes: int = SPOOL_MAX_BYTES) -> None:
        self._buffer: IO[bytes] = tempfile.SpooledTemporaryFile(
            max_size=max_memory_bytes
        )
        self.image_key = ""
        self.declared_size = 0
        self.received_bytes = 0
        self.threshold = 0.0

    def consume(self, chunks: Iterator[dedup_pb2.ImageChunk]) -> None:
        """Drains the request stream, capturing key, size and threshold metadata."""
        for chunk in chunks:
            if not self.image_key and chunk.image_key:
                self.image_key = chunk.image_key
                self.threshold = chunk.threshold
            if not self.declared_size and chunk.total_size:
                self.declared_size = chunk.total_size
            if chunk.data:
                self.received_bytes += self._buffer.write(chunk.data)

    def reader(self) -> IO[bytes]:
        """Returns the buffer rewound to the first byte."""
        self._buffer.seek(0)
        return self._buffer

    @property
    def spilled_to_disk(self) -> bool:
        """Returns True once the buffer has overflowed from memory onto disk."""
        return bool(getattr(self._buffer, "_rolled", False))

    def close(self) -> None:
        """Releases the buffer along with any on-disk spill."""
        self._buffer.close()

    def __enter__(self) -> SpooledImage:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()


@dataclass
class ImageDedupService(dedup_pb2_grpc.ImageDedupServiceServicer):
    """Scores, embeds and deduplicates images arriving as a client-side stream."""

    repository: EmbeddingRepository
    embedder: SscdEmbedder
    scorer: QualityScorer

    def ProcessImageStream(
        self,
        request_iterator: Iterator[dedup_pb2.ImageChunk],
        context: grpc.ServicerContext,
    ) -> dedup_pb2.ProcessImageResponse:
        """Handles the client-streaming dedup RPC and returns the final decision."""
        with SpooledImage() as image:
            image.consume(request_iterator)
            self._validate(image, context)
            log.info(
                "received image stream key=%s bytes=%d declared=%d spilled=%s",
                image.image_key,
                image.received_bytes,
                image.declared_size,
                image.spilled_to_disk,
            )
            quality = self.scorer.score(image.reader())
            embedding = self.embedder.embed(image.reader())

        log.info(
            "quality key=%s total=%.4f resolution=%.4f sharpness=%.4f contrast=%.4f",
            image.image_key,
            quality.total,
            quality.resolution,
            quality.sharpness,
            quality.contrast,
        )

        outcome = self.repository.resolve(
            image.image_key, embedding, quality.total, image.threshold
        )
        log.info(
            "dedup decision key=%s status=%s distance=%.4f existing=%s",
            image.image_key,
            outcome.status,
            outcome.distance,
            outcome.existing_image_key or "-",
        )
        return dedup_pb2.ProcessImageResponse(
            status=outcome.status,
            image_key=image.image_key,
            quality_score=quality.total,
            distance=outcome.distance,
            existing_image_key=outcome.existing_image_key,
        )

    @staticmethod
    def _validate(image: SpooledImage, context: grpc.ServicerContext) -> None:
        """Aborts the RPC when the stream carried no key or no payload bytes."""
        if not image.image_key:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "image_key is required")
        if image.received_bytes == 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "image stream contained no data"
            )


@dataclass
class AIGrpcServer:
    """Context-managed gRPC server owning the worker's pooled resources."""

    OPTIONS: ClassVar[ServerOptions] = (
        ("grpc.max_receive_message_length", GRPC_MAX_MESSAGE_BYTES),
        ("grpc.max_send_message_length", GRPC_MAX_MESSAGE_BYTES),
    )
    SHUTDOWN_GRACE_SECONDS: ClassVar[float] = GRPC_SHUTDOWN_GRACE_SECONDS

    port: int = GRPC_PORT
    max_workers: int = GRPC_MAX_WORKERS
    repository: EmbeddingRepository = field(default_factory=EmbeddingRepository)
    embedder: SscdEmbedder = field(default_factory=get_embedder)
    scorer: QualityScorer = field(default_factory=get_scorer)
    _stack: ExitStack = field(default_factory=ExitStack, init=False, repr=False)
    _server: Optional[grpc.Server] = field(default=None, init=False, repr=False)

    def __enter__(self) -> AIGrpcServer:
        """Warms dependencies, binds the port and arms the teardown stack."""
        with ExitStack() as stack:
            self._prepare()
            executor = stack.enter_context(
                ThreadPoolExecutor(max_workers=self.max_workers)
            )
            self._server = self._build(executor)
            stack.callback(self._shutdown)
            self._stack = stack.pop_all()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self._stack.close()

    def start(self) -> None:
        """Begins accepting requests on the bound port."""
        self._require_server().start()
        log.info("ai_worker listening on port %d", self.port)

    def wait_for_termination(self) -> None:
        """Blocks until the server stops serving."""
        self._require_server().wait_for_termination()

    def _prepare(self) -> None:
        """Ensures the database schema exists and the model is resident."""
        self.repository.init_schema()
        log.debug("pgvector schema ready")
        self.embedder.load()
        log.debug("sscd model warmed")

    def _build(self, executor: ThreadPoolExecutor) -> grpc.Server:
        """Creates the server, registers the dedup service and binds the port."""
        server = grpc.server(executor, options=self.OPTIONS)
        dedup_pb2_grpc.add_ImageDedupServiceServicer_to_server(
            ImageDedupService(self.repository, self.embedder, self.scorer), server
        )
        server.add_insecure_port(f"[::]:{self.port}")
        return server

    def _shutdown(self) -> None:
        """Stops the server, giving in-flight RPCs a grace period to finish."""
        server = self._server
        self._server = None
        if server is None:
            return
        server.stop(self.SHUTDOWN_GRACE_SECONDS).wait(self.SHUTDOWN_GRACE_SECONDS)
        log.debug("ai_worker server stopped")

    def _require_server(self) -> grpc.Server:
        """Returns the bound server, failing when used outside the context."""
        if self._server is None:
            raise RuntimeError("AIGrpcServer must be entered before use")
        return self._server


def install_shutdown_signals(event: threading.Event) -> None:
    """Routes SIGINT and SIGTERM to the shutdown event instead of killing us."""

    def handler(_number: int, _frame: Optional[FrameType]) -> None:
        event.set()

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, handler)


def main() -> None:
    """Runs the worker until a signal requests a graceful shutdown."""
    setup_logging()
    shutdown = threading.Event()
    install_shutdown_signals(shutdown)

    with AIGrpcServer() as server:
        server.start()
        shutdown.wait()
        log.info("shutdown signal received")


if __name__ == "__main__":
    main()
