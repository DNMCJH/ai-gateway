import httpx

from app.providers.openai_compat import OpenAICompatibleProvider
from app.config import settings


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"
    display_name = "Ollama (Local)"

    def __init__(self):
        super().__init__(
            base_url=settings.ollama_base_url,
            api_key="ollama",
        )
        self._models_cache: list[dict] = []

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    async def refresh_models(self):
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            self._models_cache = [
                {"id": m["name"], "name": m["name"], "owned_by": "ollama"}
                for m in data.get("models", [])
            ]
        except Exception:
            self._models_cache = []

    def list_models(self) -> list[dict]:
        return self._models_cache

    def get_cost_per_token(self, model: str) -> dict:
        return {"input": 0.0, "output": 0.0}

    async def is_available(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags")
            return resp.status_code == 200
        except Exception:
            return False
