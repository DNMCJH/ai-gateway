import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.core.limiter import TokenBucketLimiter
from app.core.key_pool import KeyPool
from app.core.router import (
    SmartRouter, RoundRobinStrategy, CostOptimizedStrategy,
    CapabilityBasedStrategy, STRATEGIES,
)
from app.core.cache import _cache_key, _cosine_similarity
from app.schemas.chat import ChatCompletionRequest, ChatMessage


# --- TokenBucketLimiter ---

class TestTokenBucketLimiter:
    def test_acquire_within_limit(self):
        limiter = TokenBucketLimiter(rpm=5)
        for _ in range(5):
            assert limiter.acquire("test") is True
        assert limiter.acquire("test") is False

    def test_remaining(self):
        limiter = TokenBucketLimiter(rpm=10)
        assert limiter.remaining("key1") == 10
        limiter.acquire("key1")
        assert limiter.remaining("key1") == 9

    def test_has_tokens(self):
        limiter = TokenBucketLimiter(rpm=1)
        assert limiter.has_tokens("k") is True
        limiter.acquire("k")
        assert limiter.has_tokens("k") is False

    def test_refill_over_time(self):
        limiter = TokenBucketLimiter(rpm=60)
        limiter.acquire("k")
        initial = limiter.remaining("k")
        limiter._last_refill["k"] -= 1.0
        assert limiter.remaining("k") > initial

    def test_independent_keys(self):
        limiter = TokenBucketLimiter(rpm=2)
        limiter.acquire("a")
        limiter.acquire("a")
        assert limiter.acquire("a") is False
        assert limiter.acquire("b") is True


# --- KeyPool ---

class TestKeyPool:
    def test_from_csv(self):
        pool = KeyPool.from_csv("sk-1, sk-2, sk-3")
        assert pool.total == 3

    def test_round_robin(self):
        pool = KeyPool.from_csv("a,b,c")
        keys = [pool.next_key() for _ in range(6)]
        assert keys == ["a", "b", "c", "a", "b", "c"]

    def test_disable_key(self):
        pool = KeyPool.from_csv("a,b")
        pool.disable_key("a", seconds=60)
        assert pool.next_key() == "b"
        assert pool.next_key() == "b"
        assert pool.available_count == 1

    def test_disable_expires(self):
        pool = KeyPool.from_csv("a,b")
        pool.disable_key("a", seconds=1)
        pool._disabled["a"] = time.time() - 1
        assert pool.available_count == 2
        assert pool.next_key() in ("a", "b")

    def test_all_disabled_raises(self):
        pool = KeyPool.from_csv("a")
        pool.disable_key("a", seconds=60)
        with pytest.raises(RuntimeError, match="All API keys"):
            pool.next_key()

    def test_empty_csv(self):
        pool = KeyPool.from_csv("")
        assert not pool
        assert pool.total == 0

    def test_bool(self):
        assert bool(KeyPool.from_csv("a")) is True
        assert bool(KeyPool.from_csv("")) is False


# --- Cache helpers ---

class TestCacheHelpers:
    def test_cache_key_deterministic(self):
        req = ChatCompletionRequest(
            model="gpt-4", messages=[ChatMessage(role="user", content="hello")]
        )
        assert _cache_key(req) == _cache_key(req)

    def test_cache_key_differs_by_model(self):
        msgs = [ChatMessage(role="user", content="hi")]
        r1 = ChatCompletionRequest(model="gpt-4", messages=msgs)
        r2 = ChatCompletionRequest(model="gpt-3.5", messages=msgs)
        assert _cache_key(r1) != _cache_key(r2)

    def test_cache_key_differs_by_content(self):
        r1 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="a")]
        )
        r2 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="b")]
        )
        assert _cache_key(r1) != _cache_key(r2)

    def test_cosine_similarity_identical(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self):
        assert abs(_cosine_similarity([1, 0], [0, 1])) < 1e-6

    def test_cosine_similarity_zero_vector(self):
        assert _cosine_similarity([0, 0], [1, 2]) == 0.0


# --- Router ---

def _make_provider(name, models, cost_output=1.0):
    p = MagicMock()
    p.name = name
    p.display_name = name.title()
    p.list_models.return_value = [{"id": m} for m in models]
    p.get_cost_per_token.return_value = {"input": cost_output * 0.5, "output": cost_output}
    return p


class TestRoundRobinStrategy:
    def test_cycles_providers(self):
        p1 = _make_provider("a", ["m1"])
        p2 = _make_provider("b", ["m2"])
        strategy = RoundRobinStrategy()
        req = ChatCompletionRequest(model="auto", messages=[ChatMessage(role="user", content="hi")])
        results = [strategy.select(req, [p1, p2]) for _ in range(4)]
        assert results[0] == (p1, "m1")
        assert results[1] == (p2, "m2")
        assert results[2] == (p1, "m1")

    def test_empty_providers_raises(self):
        strategy = RoundRobinStrategy()
        req = ChatCompletionRequest(model="auto", messages=[ChatMessage(role="user", content="hi")])
        with pytest.raises(ValueError):
            strategy.select(req, [])


class TestCostOptimizedStrategy:
    def test_picks_cheapest(self):
        cheap = _make_provider("cheap", ["m1"], cost_output=0.1)
        expensive = _make_provider("exp", ["m2"], cost_output=10.0)
        strategy = CostOptimizedStrategy()
        req = ChatCompletionRequest(model="auto", messages=[ChatMessage(role="user", content="hi")])
        provider, model = strategy.select(req, [expensive, cheap])
        assert provider.name == "cheap"


class TestCapabilityStrategy:
    def test_prefers_code_providers(self):
        anthropic = _make_provider("anthropic", ["claude"], cost_output=5.0)
        openai = _make_provider("openai", ["gpt4"], cost_output=1.0)
        strategy = CapabilityBasedStrategy()
        req = ChatCompletionRequest(
            model="auto",
            messages=[ChatMessage(role="user", content="```python\ndef foo(): pass")]
        )
        provider, _ = strategy.select(req, [openai, anthropic])
        assert provider.name == "anthropic"


class TestSmartRouter:
    def test_default_strategy(self):
        router = SmartRouter("round-robin")
        assert router.strategy_name == "round-robin"

    def test_set_strategy(self):
        router = SmartRouter("round-robin")
        router.set_strategy("cost")
        assert router.strategy_name == "cost"

    def test_invalid_strategy_raises(self):
        router = SmartRouter("round-robin")
        with pytest.raises(ValueError):
            router.set_strategy("nonexistent")

    def test_route_with_override(self):
        p1 = _make_provider("cheap", ["m1"], cost_output=0.1)
        p2 = _make_provider("exp", ["m2"], cost_output=10.0)
        router = SmartRouter("round-robin")
        req = ChatCompletionRequest(model="auto", messages=[ChatMessage(role="user", content="hi")])
        provider, _ = router.route(req, [p2, p1], strategy_name="cost")
        assert provider.name == "cheap"
