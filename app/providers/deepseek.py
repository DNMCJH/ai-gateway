from app.providers.openai_compat import OpenAICompatibleProvider
from app.config import settings

DEEPSEEK_MODELS = [
    {"id": "deepseek-chat", "name": "DeepSeek Chat", "owned_by": "deepseek"},
    {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "owned_by": "deepseek"},
]

DEEPSEEK_PRICING = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    display_name = "DeepSeek"

    def __init__(self):
        super().__init__(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
        )

    def list_models(self) -> list[dict]:
        return DEEPSEEK_MODELS

    def get_cost_per_token(self, model: str) -> dict:
        return DEEPSEEK_PRICING.get(model, {"input": 0.0, "output": 0.0})
