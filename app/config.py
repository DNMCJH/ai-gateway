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

    model_config = {"env_file": ".env"}


settings = Settings()
