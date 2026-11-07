"""No-reference image quality score constants."""

from typing import Final

QUALITY_RESOLUTION_WEIGHT: Final[float] = 0.5
QUALITY_SHARPNESS_WEIGHT: Final[float] = 0.3
QUALITY_CONTRAST_WEIGHT: Final[float] = 0.2
QUALITY_RESOLUTION_BASELINE_PIXELS: Final[int] = 3840 * 2160
QUALITY_SHARPNESS_REFERENCE: Final[float] = 600.0
QUALITY_CONTRAST_REFERENCE: Final[float] = 80.0
QUALITY_ANALYSIS_CANVAS_PX: Final[int] = 512
