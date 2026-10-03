"""No-reference image quality scoring used as the deduplication information gain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import IO, ClassVar, Final, Tuple

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image

from const.quality import (
    QUALITY_ANALYSIS_CANVAS_PX,
    QUALITY_CONTRAST_REFERENCE,
    QUALITY_CONTRAST_WEIGHT,
    QUALITY_RESOLUTION_BASELINE_PIXELS,
    QUALITY_RESOLUTION_WEIGHT,
    QUALITY_SHARPNESS_REFERENCE,
    QUALITY_SHARPNESS_WEIGHT,
)

GrayImage = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class QualityScore:
    """Individually normalized quality factors and their weighted total."""

    resolution: float
    sharpness: float
    contrast: float
    total: float


@dataclass(frozen=True)
class QualityScorer:
    """Scores images from resolution, Laplacian sharpness and intensity contrast."""

    RESOLUTION_WEIGHT: ClassVar[float] = QUALITY_RESOLUTION_WEIGHT
    SHARPNESS_WEIGHT: ClassVar[float] = QUALITY_SHARPNESS_WEIGHT
    CONTRAST_WEIGHT: ClassVar[float] = QUALITY_CONTRAST_WEIGHT
    RESOLUTION_BASELINE_PIXELS: ClassVar[int] = QUALITY_RESOLUTION_BASELINE_PIXELS
    SHARPNESS_REFERENCE: ClassVar[float] = QUALITY_SHARPNESS_REFERENCE
    CONTRAST_REFERENCE: ClassVar[float] = QUALITY_CONTRAST_REFERENCE
    ANALYSIS_CANVAS_PX: ClassVar[int] = QUALITY_ANALYSIS_CANVAS_PX

    def score(self, file_obj: IO[bytes]) -> QualityScore:
        """Returns the resolution-weighted quality score of the image in file_obj."""
        pixel_count, gray = self._decode(file_obj)
        resolution = _clamped_ratio(pixel_count, self.RESOLUTION_BASELINE_PIXELS)
        sharpness = _clamped_ratio(self._sharpness(gray), self.SHARPNESS_REFERENCE)
        contrast = _clamped_ratio(self._contrast(gray), self.CONTRAST_REFERENCE)
        total = (
            self.RESOLUTION_WEIGHT * resolution
            + self.SHARPNESS_WEIGHT * sharpness
            + self.CONTRAST_WEIGHT * contrast
        )
        return QualityScore(
            resolution=resolution,
            sharpness=sharpness,
            contrast=contrast,
            total=total,
        )

    @classmethod
    def _decode(cls, file_obj: IO[bytes]) -> Tuple[int, GrayImage]:
        """Returns the pre-downscale pixel count and a fixed-size grayscale canvas."""
        file_obj.seek(0)
        with Image.open(file_obj) as image:
            width, height = image.size
            image.draft("L", (cls.ANALYSIS_CANVAS_PX, cls.ANALYSIS_CANVAS_PX))
            gray_image = image.convert("L")

        canvas = gray_image.resize(
            _canvas_size(gray_image.size), Image.Resampling.BILINEAR
        )
        return width * height, np.asarray(canvas, dtype=np.uint8)

    @staticmethod
    def _sharpness(gray: GrayImage) -> float:
        """Returns the Laplacian variance, which falls as real detail is lost."""
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def _contrast(gray: GrayImage) -> float:
        """Returns the standard deviation of pixel intensities."""
        return float(gray.std())


def _canvas_size(size: Tuple[int, int]) -> Tuple[int, int]:
    """Scales both sides so the longest matches the fixed analysis canvas."""
    width, height = size
    longest = max(width, height)
    if longest <= 0:
        raise ValueError("image has a non-positive dimension")
    scale = QUALITY_ANALYSIS_CANVAS_PX / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _clamped_ratio(value: float, reference: float) -> float:
    """Divides value by reference and clamps the result into the 0..1 range."""
    if reference <= 0:
        return 0.0
    return min(max(value / reference, 0.0), 1.0)


_SCORER: Final[QualityScorer] = QualityScorer()


def get_scorer() -> QualityScorer:
    """Returns the process-wide quality scorer singleton."""
    return _SCORER
