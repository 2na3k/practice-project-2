from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator


class BloomFilter:
    __slots__ = ("_bits", "_size", "_hashes")

    def __init__(self, capacity: int = 100_000, error_rate: float = 0.01) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if not 0.0 < error_rate < 1.0:
            raise ValueError("error_rate must be between 0 and 1 exclusive")

        self._size = max(8, math.ceil(-capacity * math.log(error_rate) / math.log(2) ** 2))
        self._hashes = max(1, round(self._size / capacity * math.log(2)))
        self._bits = bytearray((self._size + 7) // 8)

    def _positions(self, value: str) -> Iterator[int]:
        digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()
        first = int.from_bytes(digest[:8], "big")
        stride = int.from_bytes(digest[8:], "big") | 1
        for i in range(self._hashes):
            yield (first + i * stride) % self._size

    def add(self, value: str) -> None:
        for position in self._positions(value):
            self._bits[position >> 3] |= 1 << (position & 7)

    def __contains__(self, value: str) -> bool:
        return all(
            self._bits[position >> 3] >> (position & 7) & 1
            for position in self._positions(value)
        )
