from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    ollama_base_url: str = "http://localhost:11434"

    default_routing_strategy: str = "round-robin"
    rate_limit_rpm: int = 60

    db_path: str = "data/gateway.db"

    # Comma-separated gateway API keys. Empty = auth disabled (dev mode).
    # Stored as raw string to avoid pydantic-settings JSON-decoding list fields.
    gateway_api_keys_raw: str = ""

    @property
    def gateway_api_keys(self) -> list[str]:
        return [k.strip() for k in self.gateway_api_keys_raw.split(",") if k.strip()]

    # Body logging — off by default (contains user prompts). Truncated to keep DB lean.
    log_request_body: bool = False
    log_response_body: bool = False
    body_log_max_chars: int = 2000

    # Response cache
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600

    # Semantic cache (uses DeepSeek embedding API — has per-call cost)
    semantic_cache_enabled: bool = False
    semantic_cache_threshold: float = 0.92

    # Provider timeout
    provider_timeout: float = 60.0

    # Retry
    max_retries: int = 2
    first_chunk_timeout: float = 10.0

    # Cache eviction
    cache_eviction_interval: int = 600

    model_config = {"env_file": ".env"}


settings = Settings()
