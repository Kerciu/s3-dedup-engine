"""SSCD model identity, preprocessing inputs and descriptor properties."""

from typing import Final, Tuple

SSCD_REPO_ID: Final[str] = "m3/sscd-copy-detection"
SSCD_MODEL_FILENAME: Final[str] = "sscd_disc_mixup.torchscript.pt"
SSCD_INPUT_SHORTEST_SIDE: Final[int] = 288
SSCD_NORMALIZE_MEAN: Final[Tuple[float, float, float]] = (0.485, 0.456, 0.406)
SSCD_NORMALIZE_STD: Final[Tuple[float, float, float]] = (0.229, 0.224, 0.225)
EMBEDDING_DIMENSIONS: Final[int] = 512
EMBEDDING_EPSILON: Final[float] = 1e-12
