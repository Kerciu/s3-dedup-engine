"""SSCD TorchScript embedder producing L2-normalized copy-detection descriptors."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import IO, ClassVar, Final, List, Optional, Tuple

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from PIL import Image

from const.sscd import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_EPSILON,
    SSCD_INPUT_SHORTEST_SIDE,
    SSCD_MODEL_FILENAME,
    SSCD_NORMALIZE_MEAN,
    SSCD_NORMALIZE_STD,
    SSCD_REPO_ID,
)
from logger import get_logger

log = get_logger()


@dataclass
class SscdEmbedder:
    """Lazily loads the SSCD TorchScript graph and embeds images from file objects."""

    DIMENSIONS: ClassVar[int] = EMBEDDING_DIMENSIONS
    SHORTEST_SIDE: ClassVar[int] = SSCD_INPUT_SHORTEST_SIDE
    NORMALIZE_MEAN: ClassVar[Tuple[float, float, float]] = SSCD_NORMALIZE_MEAN
    NORMALIZE_STD: ClassVar[Tuple[float, float, float]] = SSCD_NORMALIZE_STD

    repo_id: str = SSCD_REPO_ID
    filename: str = SSCD_MODEL_FILENAME
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _model: Optional[torch.jit.ScriptModule] = field(
        default=None, init=False, repr=False
    )

    def load(self) -> torch.jit.ScriptModule:
        """Returns the cached TorchScript model, downloading and loading it once."""
        with self._lock:
            if self._model is None:
                self._model = self._load_model()
            return self._model

    def embed(self, file_obj: IO[bytes]) -> List[float]:
        """Returns the unit-norm SSCD embedding of the image held in file_obj."""
        batch = self._preprocess(file_obj)
        model = self.load()
        with torch.inference_mode():
            output = model(batch)
        vector = self._to_unit_norm(output.reshape(-1).to(torch.float32))
        return [float(value) for value in vector.tolist()]

    def _load_model(self) -> torch.jit.ScriptModule:
        """Downloads the SSCD weights from the Hub and loads them onto the CPU."""
        weights_path = hf_hub_download(repo_id=self.repo_id, filename=self.filename)
        log.debug("loading sscd torchscript weights from %s", weights_path)
        model: torch.jit.ScriptModule = torch.jit.load(weights_path, map_location="cpu")
        model.eval()
        return model

    def _preprocess(self, file_obj: IO[bytes]) -> torch.Tensor:
        """Builds a normalized NCHW batch resized to the SSCD shortest side."""
        file_obj.seek(0)
        with Image.open(file_obj) as image:
            rgb = image.convert("RGB")

        resized = rgb.resize(self._target_size(rgb.size), Image.Resampling.BILINEAR)
        pixels = np.asarray(resized, dtype=np.float32) / 255.0
        mean = np.asarray(self.NORMALIZE_MEAN, dtype=np.float32)
        std = np.asarray(self.NORMALIZE_STD, dtype=np.float32)
        normalized = (pixels - mean) / std
        batch: torch.Tensor = torch.from_numpy(normalized).permute(2, 0, 1).unsqueeze(0)
        return batch

    @classmethod
    def _target_size(cls, size: Tuple[int, int]) -> Tuple[int, int]:
        """Scales both sides so the shortest one matches the SSCD input size."""
        width, height = size
        shortest = min(width, height)
        if shortest <= 0:
            raise ValueError("image has a non-positive dimension")
        scale = cls.SHORTEST_SIDE / shortest
        return max(1, round(width * scale)), max(1, round(height * scale))

    @classmethod
    def _to_unit_norm(cls, vector: torch.Tensor) -> torch.Tensor:
        """Rescales the descriptor to unit L2 norm, guarding against a zero norm."""
        if vector.numel() != cls.DIMENSIONS:
            raise ValueError(
                f"expected {cls.DIMENSIONS} dimensions, got {vector.numel()}"
            )
        norm: torch.Tensor = vector.norm(p=2).clamp_min(EMBEDDING_EPSILON)
        unit: torch.Tensor = vector / norm
        return unit


_EMBEDDER: Final[SscdEmbedder] = SscdEmbedder()


def get_embedder() -> SscdEmbedder:
    """Returns the process-wide SSCD embedder singleton."""
    return _EMBEDDER
