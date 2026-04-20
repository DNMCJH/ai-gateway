from app.providers.openai_compat import OpenAICompatibleProvider
from app.config import settings

OPENAI_MODELS = [
    {"id": "gpt-4o", "name": "GPT-4o", "owned_by": "openai"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "owned_by": "openai"},
    {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "owned_by": "openai"},
]

OPENAI_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
}


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    display_name = "OpenAI"

    def __init__(self):
        super().__init__(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )

    def list_models(self) -> list[dict]:
        return OPENAI_MODELS

    def get_cost_per_token(self, model: str) -> dict:
        return OPENAI_PRICING.get(model, {"input": 0.0, "output": 0.0})
