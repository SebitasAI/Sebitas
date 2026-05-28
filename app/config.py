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

    # Cheap model for delegated sub-tasks, routed via LiteLLM (provider/model form).
    cheap_model: str = "anthropic/claude-haiku-4-5"
    # Safety cap on agent loop turns.
    agent_max_iterations: int = 8

    # E2B sandbox (the SDK also reads E2B_API_KEY from the environment).
    e2b_api_key: str | None = None
    e2b_timeout_seconds: int = 300

    # Cloudflare R2 (S3-compatible) for artifacts + skill packages.
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    artifact_url_expiry: int = 3600  # signed-URL lifetime (seconds)

    # Pipedream Connect (credentialed integrations gateway). We never store the
    # provider credentials, only the connected-account reference per workspace.
    pipedream_client_id: str | None = None
    pipedream_client_secret: str | None = None
    pipedream_project_id: str | None = None
    pipedream_environment: str = "development"
    integration_action_timeout: int = 60
    # Public base URL (e.g. a cloudflared tunnel) for the Pipedream connect webhook.
    public_base_url: str | None = None
    pipedream_webhook_secret: str | None = None
    # Polling fallback for in-conversation connect auto-resume.
    connect_poll_interval: int = 5
    connect_poll_timeout: int = 180

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
