"""Tests for the context-managed gRPC server lifecycle."""

from __future__ import annotations

import signal
import threading
from typing import Tuple, cast

import pytest

from const.grpc import GRPC_MAX_MESSAGE_BYTES
from db import EmbeddingRepository
from embeddings.sscd import SscdEmbedder, get_embedder
from main import AIGrpcServer, install_shutdown_signals
from quality.score import QualityScorer, get_scorer
from tests.conftest import FakeRepository, FakeScriptModule

EPHEMERAL_PORT = 0
FakeEmbedder = Tuple[SscdEmbedder, FakeScriptModule]


def build_server(
    repository: FakeRepository,
    embedder: SscdEmbedder,
    scorer: QualityScorer,
) -> AIGrpcServer:
    """Builds a server bound to an ephemeral port with injected dependencies."""
    return AIGrpcServer(
        port=EPHEMERAL_PORT,
        max_workers=1,
        repository=cast(EmbeddingRepository, repository),
        embedder=embedder,
        scorer=scorer,
    )


def test_entering_prepares_the_schema_and_the_model(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, model = fake_embedder
    with build_server(fake_repository, embedder, get_scorer()):
        assert fake_repository.schema_calls == 1
        assert model.load_calls == 1


def test_server_is_usable_only_inside_the_context(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    server = build_server(fake_repository, embedder, get_scorer())
    with pytest.raises(RuntimeError, match="entered"):
        server.start()


def test_enter_returns_the_wrapper_not_the_grpc_server(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    server = build_server(fake_repository, embedder, get_scorer())
    with server as entered:
        assert entered is server


def test_start_and_exit_release_the_server(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    server = build_server(fake_repository, embedder, get_scorer())
    with server:
        server.start()
    with pytest.raises(RuntimeError, match="entered"):
        server.wait_for_termination()


def test_exit_is_safe_without_a_start(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    with build_server(fake_repository, embedder, get_scorer()):
        pass


def test_reentry_rebinds_a_fresh_server(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    server = build_server(fake_repository, embedder, get_scorer())
    with server:
        server.start()
    with server:
        server.start()
    assert fake_repository.schema_calls == 2


def test_preparation_failure_rolls_back_the_exit_stack(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, model = fake_embedder
    fake_repository.schema_error = RuntimeError("schema unavailable")
    server = build_server(fake_repository, embedder, get_scorer())
    with pytest.raises(RuntimeError, match="schema unavailable"):
        with server:
            pass
    assert model.load_calls == 0
    with pytest.raises(RuntimeError, match="entered"):
        server.start()


def test_body_exception_still_tears_the_server_down(
    fake_repository: FakeRepository, fake_embedder: FakeEmbedder
) -> None:
    embedder, _ = fake_embedder
    server = build_server(fake_repository, embedder, get_scorer())
    with pytest.raises(ValueError, match="boom"):
        with server:
            server.start()
            raise ValueError("boom")
    with pytest.raises(RuntimeError, match="entered"):
        server.start()


def test_shutdown_signals_set_the_event_instead_of_exiting() -> None:
    shutdown = threading.Event()
    previous = {
        number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        install_shutdown_signals(shutdown)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(int(signal.SIGTERM), None)
        assert shutdown.is_set()
    finally:
        for number, original in previous.items():
            signal.signal(number, original)


def test_options_size_messages_for_streamed_chunks() -> None:
    options = dict(AIGrpcServer.OPTIONS)
    assert options["grpc.max_receive_message_length"] == GRPC_MAX_MESSAGE_BYTES
    assert options["grpc.max_send_message_length"] == GRPC_MAX_MESSAGE_BYTES


def test_defaults_fall_back_to_the_shared_singletons() -> None:
    server = AIGrpcServer(port=EPHEMERAL_PORT)
    assert server.scorer is get_scorer()
    assert server.embedder is get_embedder()
