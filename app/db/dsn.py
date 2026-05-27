"""DSN normalization, kept dependency-free so Alembic can import it without
pulling in application settings (it only needs DATABASE_URL)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_dsn(url: str) -> tuple[str, dict]:
    """Coerce a libpq/Neon Postgres URL into an asyncpg-friendly SQLAlchemy DSN.

    - Forces the asyncpg driver (postgresql+asyncpg://).
    - Drops libpq-only query params (sslmode, channel_binding) that asyncpg
      rejects, translating sslmode != "disable" into a TLS connect-arg.

    Returns (dsn, connect_args).
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"

    query = dict(parse_qsl(parts.query))
    connect_args: dict = {}
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = True

    dsn = urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return dsn, connect_args
