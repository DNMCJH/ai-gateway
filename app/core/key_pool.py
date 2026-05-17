import time


class KeyPool:
    def __init__(self, keys: list[str]):
        self._keys = [k for k in keys if k]
        self._index = 0
        self._disabled: dict[str, float] = {}

    @classmethod
    def from_csv(cls, csv: str) -> "KeyPool":
        return cls([k.strip() for k in csv.split(",") if k.strip()])

    def __bool__(self) -> bool:
        return bool(self._keys)

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
