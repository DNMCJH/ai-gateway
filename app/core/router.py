from abc import ABC, abstractmethod

from app.providers.base import ProviderBase
from app.schemas.chat import ChatCompletionRequest


class RoutingStrategy(ABC):
    @abstractmethod
    def select(
        self, request: ChatCompletionRequest, providers: list[ProviderBase]
    ) -> ProviderBase:
        ...


class RoundRobinStrategy(RoutingStrategy):
    def __init__(self):
        self._counter = 0

    def select(self, request, providers):
        if not providers:
            raise ValueError("No providers available")
        provider = providers[self._counter % len(providers)]
        self._counter += 1
        return provider


class CostOptimizedStrategy(RoutingStrategy):
    def select(self, request, providers):
        if not providers:
            raise ValueError("No providers available")
        return min(
            providers,
            key=lambda p: p.get_cost_per_token(request.model).get("output", float("inf")),
        )


class CapabilityBasedStrategy(RoutingStrategy):
    CODE_KEYWORDS = ["```", "def ", "function ", "class ", "import "]

    def select(self, request, providers):
        if not providers:
            raise ValueError("No providers available")

        text = " ".join(m.content for m in request.messages)
        has_code = any(kw in text for kw in self.CODE_KEYWORDS)
        is_long = len(text) > 8000

        def score(p: ProviderBase) -> int:
            name = p.name
            if has_code and name in ("anthropic", "deepseek"):
                return 0
            if is_long and name == "anthropic":
                return 0
            cost = p.get_cost_per_token(request.model).get("output", 0)
            return int(cost * 100) + 1

        return min(providers, key=score)


STRATEGIES = {
    "round-robin": RoundRobinStrategy,
    "cost": CostOptimizedStrategy,
    "capability": CapabilityBasedStrategy,
}


class SmartRouter:
    def __init__(self, default_strategy: str = "round-robin"):
        self._strategy = STRATEGIES.get(default_strategy, RoundRobinStrategy)()

    def set_strategy(self, name: str):
        cls = STRATEGIES.get(name)
        if not cls:
            raise ValueError(f"Unknown strategy: {name}")
        self._strategy = cls()

    @property
    def strategy_name(self) -> str:
        return type(self._strategy).__name__

    def route(
        self, request: ChatCompletionRequest, providers: list[ProviderBase]
    ) -> ProviderBase:
        return self._strategy.select(request, providers)
