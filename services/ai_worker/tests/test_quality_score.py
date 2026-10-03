"""Tests for the resolution-weighted image quality score."""

from __future__ import annotations

import io
from typing import Callable, Tuple

import pytest

from const.quality import (
    QUALITY_ANALYSIS_CANVAS_PX,
    QUALITY_CONTRAST_WEIGHT,
    QUALITY_RESOLUTION_BASELINE_PIXELS,
    QUALITY_RESOLUTION_WEIGHT,
    QUALITY_SHARPNESS_WEIGHT,
)
from quality.score import QualityScorer, _canvas_size, _clamped_ratio, get_scorer

NamedJpeg = Callable[[str], io.BytesIO]


def test_weights_sum_to_one() -> None:
    total = (
        QUALITY_RESOLUTION_WEIGHT + QUALITY_SHARPNESS_WEIGHT + QUALITY_CONTRAST_WEIGHT
    )
    assert total == pytest.approx(1.0)


def test_resolution_is_the_dominant_weight() -> None:
    assert QUALITY_RESOLUTION_WEIGHT > QUALITY_SHARPNESS_WEIGHT
    assert QUALITY_RESOLUTION_WEIGHT > QUALITY_CONTRAST_WEIGHT


def test_score_ladder_is_monotonic_in_resolution(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    ladder = ["uhd", "hd", "sd", "tiny"]
    totals = [scorer.score(jpeg(name)).total for name in ladder]
    assert totals == sorted(totals, reverse=True), dict(zip(ladder, totals))


def test_uhd_beats_sd_thumbnail(scorer: QualityScorer, jpeg: NamedJpeg) -> None:
    assert scorer.score(jpeg("uhd")).total > scorer.score(jpeg("sd")).total


def test_sharpness_decides_at_equal_pixel_count(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    sharp = scorer.score(jpeg("hd"))
    blurred = scorer.score(jpeg("hd_blurred"))
    assert sharp.resolution == pytest.approx(blurred.resolution)
    assert sharp.sharpness > blurred.sharpness
    assert sharp.total > blurred.total


def test_factors_stay_within_unit_range(scorer: QualityScorer, jpeg: NamedJpeg) -> None:
    for name in ("uhd", "hd", "sd", "tiny", "hd_blurred"):
        score = scorer.score(jpeg(name))
        for factor, value in vars(score).items():
            assert 0.0 <= value <= 1.0, f"{name}.{factor} out of range: {value}"


def test_resolution_saturates_at_the_4k_baseline(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    assert scorer.score(jpeg("uhd")).resolution == pytest.approx(1.0)


def test_resolution_uses_pixels_from_before_downscaling(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    score = scorer.score(jpeg("hd"))
    expected = (1920 * 1080) / QUALITY_RESOLUTION_BASELINE_PIXELS
    assert score.resolution == pytest.approx(expected)


def test_score_rewinds_a_consumed_buffer(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    buffer = jpeg("hd")
    buffer.read()
    assert scorer.score(buffer).resolution > 0.0


def test_score_is_deterministic(scorer: QualityScorer, jpeg: NamedJpeg) -> None:
    first = scorer.score(jpeg("hd"))
    second = scorer.score(jpeg("hd"))
    assert first == second


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1024, 512), (QUALITY_ANALYSIS_CANVAS_PX, QUALITY_ANALYSIS_CANVAS_PX // 2)),
        ((512, 1024), (QUALITY_ANALYSIS_CANVAS_PX // 2, QUALITY_ANALYSIS_CANVAS_PX)),
        ((256, 256), (QUALITY_ANALYSIS_CANVAS_PX, QUALITY_ANALYSIS_CANVAS_PX)),
    ],
)
def test_canvas_size_scales_longest_side(
    size: Tuple[int, int], expected: Tuple[int, int]
) -> None:
    assert _canvas_size(size) == expected


def test_canvas_size_never_collapses_to_zero() -> None:
    assert _canvas_size((4000, 1)) == (QUALITY_ANALYSIS_CANVAS_PX, 1)


def test_canvas_size_rejects_empty_images() -> None:
    with pytest.raises(ValueError):
        _canvas_size((0, 0))


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        (50.0, 100.0, 0.5),
        (250.0, 100.0, 1.0),
        (-5.0, 100.0, 0.0),
        (10.0, 0.0, 0.0),
    ],
)
def test_clamped_ratio(value: float, reference: float, expected: float) -> None:
    assert _clamped_ratio(value, reference) == pytest.approx(expected)


def test_scorer_singleton_is_shared() -> None:
    assert get_scorer() is get_scorer()


def test_score_totals_match_the_weighted_sum(
    scorer: QualityScorer, jpeg: NamedJpeg
) -> None:
    score = scorer.score(jpeg("hd"))
    expected = (
        QUALITY_RESOLUTION_WEIGHT * score.resolution
        + QUALITY_SHARPNESS_WEIGHT * score.sharpness
        + QUALITY_CONTRAST_WEIGHT * score.contrast
    )
    assert score.total == pytest.approx(expected)
