from abc import ABC, abstractmethod

from app.providers.base import ProviderBase
from app.schemas.chat import ChatCompletionRequest


def _candidates(providers: list[ProviderBase]) -> list[tuple[ProviderBase, dict]]:
    return [(p, m) for p in providers for m in p.list_models()]


class RoutingStrategy(ABC):
    @abstractmethod
    def select(
        self, request: ChatCompletionRequest, providers: list[ProviderBase]
    ) -> tuple[ProviderBase, str]:
        ...


class RoundRobinStrategy(RoutingStrategy):
    def __init__(self):
        self._counter = 0

    def select(self, request, providers):
        available = [p for p in providers if p.list_models()]
        if not available:
            raise ValueError("No providers with models available")
        provider = available[self._counter % len(available)]
        self._counter += 1
        return provider, provider.list_models()[0]["id"]


class CostOptimizedStrategy(RoutingStrategy):
    def select(self, request, providers):
        candidates = _candidates(providers)
        if not candidates:
            raise ValueError("No providers with models available")

        def cost(pair):
            p, m = pair
            return p.get_cost_per_token(m["id"]).get("output", float("inf"))

        provider, model = min(candidates, key=cost)
        return provider, model["id"]


class CapabilityBasedStrategy(RoutingStrategy):
    CODE_KEYWORDS = ["```", "def ", "function ", "class ", "import "]
    CAPABLE_PROVIDERS = {"anthropic", "deepseek"}
    LONG_CONTEXT_PROVIDERS = {"anthropic"}

    def select(self, request, providers):
        candidates = _candidates(providers)
        if not candidates:
            raise ValueError("No providers with models available")

        text = " ".join(m.content for m in request.messages)
        has_code = any(kw in text for kw in self.CODE_KEYWORDS)
        is_long = len(text) > 8000

        def score(pair):
            p, m = pair
            cost = p.get_cost_per_token(m["id"]).get("output", 0)
            preferred = 0
            if has_code and p.name in self.CAPABLE_PROVIDERS:
                preferred -= 1000
            if is_long and p.name in self.LONG_CONTEXT_PROVIDERS:
                preferred -= 1000
            return preferred + cost

        provider, model = min(candidates, key=score)
        return provider, model["id"]


STRATEGIES = {
    "round-robin": RoundRobinStrategy,
    "cost": CostOptimizedStrategy,
    "capability": CapabilityBasedStrategy,
}


class SmartRouter:
    def __init__(self, default_strategy: str = "round-robin"):
        self._default_name = default_strategy if default_strategy in STRATEGIES else "round-robin"
        self._strategy = STRATEGIES[self._default_name]()

    def set_strategy(self, name: str):
        cls = STRATEGIES.get(name)
        if not cls:
            raise ValueError(f"Unknown strategy: {name}")
        self._default_name = name
        self._strategy = cls()

    @property
    def strategy_name(self) -> str:
        return self._default_name

    def route(
        self,
        request: ChatCompletionRequest,
        providers: list[ProviderBase],
        strategy_name: str | None = None,
    ) -> tuple[ProviderBase, str]:
        if strategy_name and strategy_name != self._default_name:
            strategy = STRATEGIES[strategy_name]()
        else:
            strategy = self._strategy
        return strategy.select(request, providers)
