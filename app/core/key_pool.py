import time
from datetime import datetime


class KeyPool:
    def __init__(self, keys: list[str]):
        self._keys = keys
        self._index = 0
        self._disabled: dict[str, float] = {}

    def next_key(self) -> str:
        now = time.time()
        self._disabled = {k: t for k, t in self._disabled.items() if t > now}

        available = [k for k in self._keys if k not in self._disabled]
        if not available:
            raise RuntimeError("All API keys are rate-limited")

        key = available[self._index % len(available)]
        self._index += 1
        return key

    def disable_key(self, key: str, seconds: int = 60):
        self._disabled[key] = time.time() + seconds

    @property
    def total(self) -> int:
        return len(self._keys)

    @property
    def available_count(self) -> int:
        now = time.time()
        return sum(1 for k in self._keys if k not in self._disabled or self._disabled[k] <= now)
