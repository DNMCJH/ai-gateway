from app.providers.base import ProviderBase


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, ProviderBase] = {}
        self._model_to_provider: dict[str, str] = {}

    def register(self, provider: ProviderBase):
        self._providers[provider.name] = provider
        for model in provider.list_models():
            self._model_to_provider[model["id"]] = provider.name

    def get_provider(self, name: str) -> ProviderBase:
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' not registered")
        return self._providers[name]

    def get_provider_for_model(self, model: str) -> ProviderBase:
        provider_name = self._model_to_provider.get(model)
        if not provider_name:
            raise ValueError(f"No provider found for model '{model}'")
        return self._providers[provider_name]

    def list_all_models(self) -> list[dict]:
        models = []
        for provider in self._providers.values():
            models.extend(provider.list_models())
        return models

    def available_providers(self) -> list[ProviderBase]:
        return list(self._providers.values())

    def has_model(self, model: str) -> bool:
        return model in self._model_to_provider


registry = ProviderRegistry()
