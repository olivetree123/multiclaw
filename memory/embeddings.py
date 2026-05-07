from __future__ import annotations

import hashlib
import math
import re
from collections import Counter


TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class HashEmbeddingProvider:
    """Small local embedding provider used until a dedicated embedding model is configured."""

    def __init__(self, dimensions: int = 384, sparse_buckets: int = 1_000_003) -> None:
        self.dimensions = dimensions
        self.sparse_buckets = sparse_buckets

    def dense(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        return self._normalize(vector)

    def sparse(self, text: str) -> tuple[list[int], list[float]]:
        counts = Counter(self._tokens(text))
        if not counts:
            return [], []

        indices = []
        values = []
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            indices.append(int.from_bytes(digest[:4], "big") % self.sparse_buckets)
            values.append(1.0 + math.log(count))

        return indices, values

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]
