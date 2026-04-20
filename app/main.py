from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api import chat, models, admin
from app.config import settings
from app.providers.registry import registry
from app.providers.deepseek import DeepSeekProvider
from app.providers.openai import OpenAIProvider
from app.providers.anthropic import AnthropicProvider
from app.providers.ollama import OllamaProvider
from app.storage.database import init_db

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    if settings.deepseek_api_key:
        registry.register(DeepSeekProvider())
    if settings.openai_api_key:
        registry.register(OpenAIProvider())
    if settings.anthropic_api_key:
        registry.register(AnthropicProvider())

    ollama = OllamaProvider()
    if await ollama.is_available():
        await ollama.refresh_models()
        if ollama.list_models():
            registry.register(ollama)

    yield


app = FastAPI(title="AI Gateway", version="0.1.0", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(models.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok", "providers": [p.name for p in registry.available_providers()]}


@app.get("/dashboard")
async def dashboard():
    return FileResponse(WEB_DIR / "index.html")
