"""Tests for chunk spooling in the streaming dedup servicer."""

from __future__ import annotations

import os
from typing import Callable, List, cast

import grpc
import pytest

from const.grpc import SPOOL_MAX_BYTES
from main import ImageDedupService, SpooledImage
from pb import dedup_pb2
from tests.conftest import Aborted, FakeServicerContext

ChunkFactory = Callable[..., List[dedup_pb2.ImageChunk]]
PAYLOAD = os.urandom(5000)


def as_context(fake: FakeServicerContext) -> grpc.ServicerContext:
    """Presents the recording double as a gRPC servicer context."""
    return cast(grpc.ServicerContext, fake)


def test_consume_captures_metadata_from_the_first_chunk(
    chunk_factory: ChunkFactory,
) -> None:
    with SpooledImage() as image:
        image.consume(iter(chunk_factory(PAYLOAD, image_key="photo.jpg")))
        assert image.image_key == "photo.jpg"
        assert image.declared_size == len(PAYLOAD)


def test_consume_accounts_for_every_byte(chunk_factory: ChunkFactory) -> None:
    with SpooledImage() as image:
        image.consume(iter(chunk_factory(PAYLOAD, chunk_size=512)))
        assert image.received_bytes == len(PAYLOAD)


def test_consume_reassembles_the_payload_in_order(
    chunk_factory: ChunkFactory,
) -> None:
    with SpooledImage() as image:
        image.consume(iter(chunk_factory(PAYLOAD, chunk_size=256)))
        assert image.reader().read() == PAYLOAD


def test_consume_takes_metadata_from_a_later_chunk_when_missing() -> None:
    chunks = [
        dedup_pb2.ImageChunk(image_key="", total_size=0, data=b"abc"),
        dedup_pb2.ImageChunk(image_key="late.jpg", total_size=3, data=b""),
    ]
    with SpooledImage() as image:
        image.consume(iter(chunks))
        assert image.image_key == "late.jpg"
        assert image.declared_size == 3


def test_first_chunk_metadata_wins_over_later_chunks() -> None:
    chunks = [
        dedup_pb2.ImageChunk(image_key="first.jpg", total_size=10, data=b"a"),
        dedup_pb2.ImageChunk(image_key="second.jpg", total_size=99, data=b"b"),
    ]
    with SpooledImage() as image:
        image.consume(iter(chunks))
        assert image.image_key == "first.jpg"
        assert image.declared_size == 10


def test_reader_rewinds_between_consumers(chunk_factory: ChunkFactory) -> None:
    with SpooledImage() as image:
        image.consume(iter(chunk_factory(PAYLOAD)))
        assert image.reader().read() == PAYLOAD
        assert image.reader().read() == PAYLOAD


def test_empty_stream_records_no_bytes() -> None:
    with SpooledImage() as image:
        image.consume(iter([]))
        assert image.received_bytes == 0
        assert image.image_key == ""


def test_small_payload_stays_in_memory(chunk_factory: ChunkFactory) -> None:
    with SpooledImage(max_memory_bytes=len(PAYLOAD) * 2) as image:
        image.consume(iter(chunk_factory(PAYLOAD)))
        assert not image.spilled_to_disk


def test_large_payload_spills_to_disk(chunk_factory: ChunkFactory) -> None:
    with SpooledImage(max_memory_bytes=1024) as image:
        image.consume(iter(chunk_factory(PAYLOAD, chunk_size=512)))
        assert image.spilled_to_disk
        assert image.reader().read() == PAYLOAD


def test_default_spool_threshold_matches_the_constant() -> None:
    with SpooledImage() as image:
        assert not image.spilled_to_disk
    assert SPOOL_MAX_BYTES == 20 * 1024 * 1024


def test_context_manager_closes_the_buffer(chunk_factory: ChunkFactory) -> None:
    image = SpooledImage()
    with image:
        image.consume(iter(chunk_factory(PAYLOAD)))
    with pytest.raises(ValueError):
        image.reader()


def test_metadata_survives_closing(chunk_factory: ChunkFactory) -> None:
    image = SpooledImage()
    with image:
        image.consume(iter(chunk_factory(PAYLOAD, image_key="kept.jpg")))
    assert image.image_key == "kept.jpg"
    assert image.received_bytes == len(PAYLOAD)


def test_validate_rejects_a_missing_image_key(
    grpc_context: FakeServicerContext,
) -> None:
    chunk = dedup_pb2.ImageChunk(image_key="", total_size=0, data=b"xyz")
    with SpooledImage() as image:
        image.consume(iter([chunk]))
        with pytest.raises(Aborted):
            ImageDedupService._validate(image, as_context(grpc_context))
    assert grpc_context.aborts[0][0] == grpc.StatusCode.INVALID_ARGUMENT


def test_validate_rejects_an_empty_payload(
    grpc_context: FakeServicerContext,
) -> None:
    chunk = dedup_pb2.ImageChunk(image_key="a.jpg", total_size=0, data=b"")
    with SpooledImage() as image:
        image.consume(iter([chunk]))
        with pytest.raises(Aborted):
            ImageDedupService._validate(image, as_context(grpc_context))
    assert "no data" in grpc_context.aborts[0][1]


def test_validate_accepts_a_complete_stream(
    grpc_context: FakeServicerContext, chunk_factory: ChunkFactory
) -> None:
    with SpooledImage() as image:
        image.consume(iter(chunk_factory(PAYLOAD, image_key="ok.jpg")))
        ImageDedupService._validate(image, as_context(grpc_context))
    assert grpc_context.aborts == []
