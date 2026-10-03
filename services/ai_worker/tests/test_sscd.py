"""Tests for SSCD preprocessing and descriptor normalization."""

from __future__ import annotations

import io
import math
from typing import Callable, Tuple

import pytest
import torch

from const.sscd import EMBEDDING_DIMENSIONS, SSCD_INPUT_SHORTEST_SIDE
from embeddings.sscd import SscdEmbedder, get_embedder
from tests.conftest import FakeScriptModule

NamedJpeg = Callable[[str], io.BytesIO]
FakeEmbedder = Tuple[SscdEmbedder, FakeScriptModule]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1920, 1080), (512, 288)),
        ((1080, 1920), (288, 512)),
        ((288, 288), (288, 288)),
        ((144, 144), (288, 288)),
    ],
)
def test_target_size_scales_shortest_side(
    size: Tuple[int, int], expected: Tuple[int, int]
) -> None:
    assert SscdEmbedder._target_size(size) == expected


def test_target_size_preserves_aspect_ratio() -> None:
    width, height = SscdEmbedder._target_size((1600, 900))
    assert min(width, height) == SSCD_INPUT_SHORTEST_SIDE
    assert width / height == pytest.approx(1600 / 900, rel=1e-2)


def test_target_size_rejects_empty_images() -> None:
    with pytest.raises(ValueError):
        SscdEmbedder._target_size((0, 100))


def test_to_unit_norm_returns_unit_length() -> None:
    vector = torch.full((EMBEDDING_DIMENSIONS,), 3.0)
    assert float(SscdEmbedder._to_unit_norm(vector).norm()) == pytest.approx(1.0)


def test_to_unit_norm_rejects_wrong_dimensions() -> None:
    with pytest.raises(ValueError):
        SscdEmbedder._to_unit_norm(torch.zeros(7))


def test_to_unit_norm_survives_a_zero_vector() -> None:
    result = SscdEmbedder._to_unit_norm(torch.zeros(EMBEDDING_DIMENSIONS))
    assert not bool(torch.isnan(result).any())


def test_embed_returns_a_normalized_descriptor(
    fake_embedder: FakeEmbedder, jpeg: NamedJpeg
) -> None:
    embedder, _ = fake_embedder
    embedding = embedder.embed(jpeg("hd"))
    assert len(embedding) == EMBEDDING_DIMENSIONS
    assert all(isinstance(value, float) for value in embedding)
    norm = math.sqrt(sum(value * value for value in embedding))
    assert norm == pytest.approx(1.0, abs=1e-5)


def test_embed_feeds_an_nchw_batch_at_the_sscd_input_size(
    fake_embedder: FakeEmbedder, jpeg: NamedJpeg
) -> None:
    embedder, model = fake_embedder
    embedder.embed(jpeg("hd"))
    batch, channels, height, width = model.input_shapes[0]
    assert (batch, channels) == (1, 3)
    assert min(height, width) == SSCD_INPUT_SHORTEST_SIDE


def test_embed_rewinds_a_consumed_buffer(
    fake_embedder: FakeEmbedder, jpeg: NamedJpeg
) -> None:
    embedder, _ = fake_embedder
    buffer = jpeg("hd")
    buffer.read()
    assert len(embedder.embed(buffer)) == EMBEDDING_DIMENSIONS


def test_model_is_loaded_once_and_cached(fake_embedder: FakeEmbedder) -> None:
    embedder, model = fake_embedder
    assert embedder.load() is model
    assert embedder.load() is model
    assert model.load_calls == 1


def test_preprocess_normalizes_away_from_raw_pixel_range(
    fake_embedder: FakeEmbedder, jpeg: NamedJpeg
) -> None:
    embedder, _ = fake_embedder
    batch = embedder._preprocess(jpeg("hd"))
    assert batch.dtype == torch.float32
    assert float(batch.min()) < 0.0


def test_embedder_singleton_is_shared() -> None:
    assert get_embedder() is get_embedder()
