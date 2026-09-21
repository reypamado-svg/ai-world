"""Stable named pseudo-random streams."""

from __future__ import annotations

import hashlib

import numpy as np


class StableRng:
    """Derive independent PCG64 streams from a run seed and stable name."""

    def __init__(self, root_seed: int) -> None:
        self.root_seed = root_seed

    def stream(self, name: str) -> np.random.Generator:
        if not name:
            raise ValueError("stream name must be non-empty")
        material = f"{self.root_seed}:{name}".encode()
        seed = int.from_bytes(hashlib.sha256(material).digest()[:16], "big")
        return np.random.Generator(np.random.PCG64(seed))

