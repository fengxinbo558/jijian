"""Local, dependency-free vector features with an honest capability label."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable, List, Sequence


class LocalFeatureEmbeddingProvider:
    """Create normalized hashed word/character-ngram vectors entirely on device.

    This is a deterministic local vector fallback, not a pretrained semantic model.
    The capability label is persisted so the UI never presents it as model semantics.
    """

    provider_key = "builtin-local-feature-vector"
    capability = "local_feature_vector"

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = max(64, int(dimensions))

    @staticmethod
    def _features(text: str) -> Iterable[str]:
        lowered = text.lower()
        for token in re.findall(r"[a-z0-9_./:-]+", lowered):
            yield "w:" + token
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        for size in (2, 3):
            for index in range(max(0, len(chinese) - size + 1)):
                yield f"c{size}:" + chinese[index : index + size]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        result: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for feature in self._features(str(text)):
                digest = hashlib.sha256(feature.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            result.append([value / norm for value in vector] if norm else vector)
        return result

    @staticmethod
    def similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
