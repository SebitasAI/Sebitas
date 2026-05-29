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
    # bot_token now lives per-workspace in DB (slice 4B-* multi-tenant);
    # this stays optional for backward compat + the backfill CLI step.
    slack_bot_token: str | None = None  # xoxb-... (legacy single-workspace fallback)
    slack_app_token: str  # xapp-... (Socket Mode app-level token; one per app, multi-workspace)

    # Fernet key for encrypting per-workspace bot tokens at rest. Generate with
    # `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
    workspace_token_encryption_key: str | None = None

    # OAuth install flow (slice C1). Fill these from your Slack app dashboard:
    # Basic Information -> App Credentials.
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    # Signing Secret is required to verify Slack OAuth callbacks (and HTTP
    # events later when we move off Socket Mode).
    slack_signing_secret: str | None = None
    # Comma-separated bot scopes for the install link. Defaults to the set we
    # use today; expand carefully (each new scope = Slack reinstall for every
    # existing workspace).
    slack_bot_scopes: str = (
        "app_mentions:read,chat:write,im:history,im:read,mpim:history,mpim:read,"
        "channels:history,channels:read,groups:history,groups:read,"
        "reactions:read,reactions:write,files:read,users:read,users:read.email"
    )

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
    # Slack file ingest limits. The per-file generic cap covers images / PDFs /
    # text / audio. Video gets a separate, much larger cap because we don't
    # send the raw video anywhere -- we extract audio (typically <10% of the
    # original size) and then chunk that into <=25MB pieces for Whisper.
    attachment_max_file_bytes: int = 50 * 1024 * 1024           # 50 MB
    attachment_max_video_bytes: int = 500 * 1024 * 1024         # 500 MB
    attachment_max_total_bytes: int = 1024 * 1024 * 1024        # 1 GB / msg
    attachment_max_text_chars: int = 200_000
    # Whisper hard per-call limit; used inside transcription.py to decide when
    # to chunk. Independent of the per-file caps above.
    attachment_max_audio_bytes: int = 25 * 1024 * 1024

    # Cloudflare Workers AI (used for Whisper transcription). Token must have
    # the Workers AI:Read scope on the account.
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None

    # Datadog (HTTP-direct query tool, covers APM endpoints the Pipedream
    # connector doesn't expose). Generate keys at Datadog -> Organization
    # Settings -> API Keys + Application Keys.
    dd_api_key: str | None = None
    dd_app_key: str | None = None
    # API host. US1 default; change to datadoghq.eu / us3.datadoghq.com /
    # us5.datadoghq.com / ap1.datadoghq.com depending on your Datadog region.
    dd_site: str = "datadoghq.com"

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

    # Composio (second integration provider). Used by ComposioProvider for apps
    # where Composio has deeper coverage than Pipedream's connector (Metabase,
    # for now). When the API key is unset, the gateway falls back to Pipedream
    # for every app — no crash, just narrower coverage.
    composio_api_key: str | None = None
    # Composio's API base; override only if you're on their preview / EU stack.
    composio_base_url: str = "https://backend.composio.dev/api/v3"
    composio_webhook_secret: str | None = None

    # Spaces (slice 4B). The shared-deployment Convex backend uses these three;
    # if any is missing the platform falls back to MockSpaceBackend at startup.
    convex_url: str | None = None              # e.g. https://<slug>.convex.cloud
    # Convex's standard naming -- found at dashboard.convex.dev -> Settings ->
    # Deploy Keys. Used in `Authorization: Convex <key>` for /api/mutation calls.
    convex_deploy_key: str | None = None
    convex_hosting_site_url: str | None = None # public site URL of Convex Hosting (frontend)
    internal_spaces_token: str | None = None   # shared secret with Convex refresh action

    # Clerk (slice 4B-iii). Frontend uses publishable_key; Python backend uses
    # secret_key to resolve email -> user_id at deploy time; jwt_issuer is the
    # URL Convex Auth validates JWTs against (must also be set in the Convex
    # dashboard env vars, not just here).
    clerk_publishable_key: str | None = None
    clerk_secret_key: str | None = None
    clerk_jwt_issuer: str | None = None

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
