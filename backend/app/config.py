"""Central configuration for the EngineerOS core.

Settings are read from environment variables / a local ``.env`` file. Every module
receives the same ``Settings`` instance, so cross-cutting concerns (storage, AI,
crawler defaults) are configured in one place.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor storage defaults to the backend directory (not the CWD) so the API server and
# the CLI share one database/artifact store no matter where they are invoked from.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Storage ---
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{(BACKEND_DIR / 'engineeros.db').as_posix()}",
        alias="DATABASE_URL",
    )
    artifacts_dir: Path = Field(default=BACKEND_DIR / "artifacts", alias="ARTIFACTS_DIR")

    # --- HTTP ---
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000", alias="CORS_ORIGINS"
    )

    # --- Crawler defaults ---
    crawl_max_pages: int = Field(default=25, alias="CRAWL_MAX_PAGES")
    crawl_max_depth: int = Field(default=3, alias="CRAWL_MAX_DEPTH")
    crawl_timeout_ms: int = Field(default=30000, alias="CRAWL_TIMEOUT_MS")
    crawl_concurrency: int = Field(default=4, alias="CRAWL_CONCURRENCY")
    respect_robots: bool = Field(default=True, alias="RESPECT_ROBOTS")

    # --- Browser (Playwright) ---
    # WAFs (Akamai/Cloudflare) block headless browsers: headless Chrome sends a
    # "HeadlessChrome" User-Agent and other bot tells. Running headed with the real
    # installed Chrome ("chrome" channel) presents as a normal user and gets through.
    # Set BROWSER_HEADED=false / BROWSER_CHANNEL="" to revert to fast headless Chromium.
    browser_channel: str = Field(default="", alias="BROWSER_CHANNEL")  # "", "chrome", "msedge"
    browser_headed: bool = Field(default=False, alias="BROWSER_HEADED")

    # --- Lighthouse ---
    lighthouse_bin: str = Field(default="lighthouse", alias="LIGHTHOUSE_BIN")
    enable_lighthouse: bool = Field(default=True, alias="ENABLE_LIGHTHOUSE")

    # --- Shared AI layer ---
    ai_provider: str = Field(default="none", alias="AI_PROVIDER")
    ai_model: str = Field(default="claude-opus-4-8", alias="AI_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    # Base URL for the OpenAI-compatible endpoint. Point at a local llama.cpp/LM Studio
    # server (e.g. http://localhost:8080/v1) to run a local model, or leave as the
    # OpenAI cloud default.
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def artifacts_path(self, *parts: str) -> Path:
        path = self.artifacts_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings
