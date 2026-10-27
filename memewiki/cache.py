"""带过期时间和容量上限的内存缓存。"""

from __future__ import annotations

import time
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(self, ttl: float, max_size: int = 300) -> None:
        self.ttl = ttl
        self.max_size = max_size
        self._data: dict[str, tuple[float, V]] = {}

    def get(self, key: str, now: float | None = None) -> V | None:
        hit = self._data.get(key)
        if hit and hit[0] > (time.time() if now is None else now):
            return hit[1]
        return None

    def remaining(self, key: str, now: float | None = None) -> float:
        hit = self._data.get(key)
        return max(0.0, hit[0] - (time.time() if now is None else now)) if hit else 0.0

    def put(self, key: str, value: V, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if key not in self._data and len(self._data) >= self.max_size:
            # 先清过期项；还满就踢掉最早过期的那条
            for k, (exp, _) in list(self._data.items()):
                if exp <= now:
                    self._data.pop(k, None)
            if len(self._data) >= self.max_size:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
        self._data[key] = (now + self.ttl, value)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
