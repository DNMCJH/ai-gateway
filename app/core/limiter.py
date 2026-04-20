import time


class TokenBucketLimiter:
    def __init__(self, rpm: int = 60):
        self._rpm = rpm
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def _refill(self, key: str):
        now = time.time()
        last = self._last_refill.get(key, now)
        elapsed = now - last
        refill = elapsed * (self._rpm / 60.0)
        self._tokens[key] = min(self._rpm, self._tokens.get(key, self._rpm) + refill)
        self._last_refill[key] = now

    def acquire(self, key: str) -> bool:
        self._refill(key)
        if self._tokens.get(key, 0) >= 1:
            self._tokens[key] -= 1
            return True
        return False

    def remaining(self, key: str) -> int:
        self._refill(key)
        return int(self._tokens.get(key, self._rpm))
