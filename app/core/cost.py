from app.providers.registry import registry


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    for provider in registry.available_providers():
        prices = provider.get_cost_per_token(model)
        if prices.get("input", 0) > 0 or prices.get("output", 0) > 0:
            return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
    return 0.0
