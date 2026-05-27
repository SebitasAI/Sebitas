"""Application settings (12-factor: read entirely from the environment).

Doppler injects these via `doppler run`. No .env file is read or committed.

Langfuse credentials (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL)
are consumed directly by the Langfuse SDK from the environment and are
intentionally not declared here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file=None: do not read any .env file; rely on the process environment
    # (which Doppler populates). Unknown env vars are ignored.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Anthropic
    anthropic_api_key: str

    # Postgres (Neon). Accepts a standard libpq URL (postgresql://...); the
    # engine layer normalizes it to the asyncpg driver.
    database_url: str

    # Slack (Socket Mode)
    slack_bot_token: str  # xoxb-...
    slack_app_token: str  # xapp-... (Socket Mode app-level token)

    # Model / runtime knobs
    claude_model: str = "claude-opus-4-7"
    claude_max_tokens: int = 8000
    claude_effort: str = "medium"  # low | medium | high | xhigh | max

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
