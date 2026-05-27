# Sebitas

Plataforma de agente de IA **agnóstica al caso de uso** que vive en Slack (estilo
"AI coworker"), multi-tenant B2B. Este repo es el **primer slice**: esqueleto del
core en Python + un bot de Slack que recibe un mensaje y responde con Claude.

No incluye (aún): loop de agente, tools, sandbox, integraciones, skills ni Spaces.

## Stack

- Python 3.12, FastAPI (async), dependencias con **uv**
- SQLAlchemy async + Alembic, Postgres en **Neon** (extensión `pgvector` habilitada)
- Slack Bolt (Socket Mode) para desarrollo local
- Anthropic SDK (`anthropic`) con `claude-opus-4-7`
- Langfuse para tracing del LLM
- pydantic-settings (config) + structlog (logs JSON)
- Secrets vía **Doppler**: no hay archivos `.env` en el repo

## Arquitectura del slice

Un solo proceso: FastAPI levanta el `AsyncSocketModeHandler` de Slack como
conexión en el `lifespan` y expone `GET /health`. Ante un `@mention` (canales) o
un DM, el handler persiste el mensaje, llama a Claude y responde en el mismo thread.

```
app/
  config.py        settings (pydantic-settings, solo env)
  logging.py       structlog (JSON)
  main.py          FastAPI + lifespan (arranca Slack) + /health
  db/
    dsn.py         normaliza la URL de Neon a asyncpg
    engine.py      engine async + sessionmaker
    session.py     get_session()
    models.py      Workspace, AppUser, Thread, Message
    repository.py  upserts / inserts
  slack/
    app.py         AsyncApp + AsyncSocketModeHandler
    handlers.py    @mention y DM -> persiste -> Claude -> responde en thread
  agent/
    claude.py      llamada a Claude (adaptive thinking, effort, cache) + Langfuse
alembic/           migración inicial (pgvector + 4 tablas)
```

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona también el intérprete Python 3.12)
- [Doppler CLI](https://docs.doppler.com/docs/install-cli), autenticado (`doppler login`)
- Un proyecto de Postgres en Neon y una Slack App con Socket Mode habilitado

## Secrets que espera Doppler

| Variable | Descripción |
|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic |
| `DATABASE_URL` | URL de Postgres de Neon (`postgresql://...`). Ver nota de Neon abajo. |
| `SLACK_BOT_TOKEN` | Token del bot, `xoxb-...` |
| `SLACK_APP_TOKEN` | App-level token para Socket Mode, `xapp-...` |
| `LANGFUSE_PUBLIC_KEY` | Public key de Langfuse |
| `LANGFUSE_SECRET_KEY` | Secret key de Langfuse |
| `LANGFUSE_BASE_URL` | Host de Langfuse (p. ej. `https://cloud.langfuse.com`) |

Las variables de Langfuse las lee el SDK directamente del entorno (no pasan por
`Settings`). Si faltan, el SDK no traza pero la app sigue funcionando.

## Configurar Doppler

```bash
doppler setup          # selecciona el proyecto y el config (dev) en este directorio
doppler secrets        # verifica que estén los secrets de arriba
```

## Instalar dependencias

```bash
uv sync                # crea el venv, baja Python 3.12 y resuelve dependencias
```

## Migrar la base de datos

```bash
doppler run -- uv run alembic upgrade head
```

Para ver el SQL sin aplicarlo (no requiere conexión):

```bash
DATABASE_URL='postgresql://u:p@host/db' uv run alembic upgrade head --sql
```

> **Nota Neon + asyncpg.** El endpoint *pooled* de Neon usa PgBouncer en modo
> transaction, que rompe los prepared statements de asyncpg. Para migraciones usa
> el endpoint **directo** (sin `-pooler` en el host), o desactiva el statement
> cache. La capa `dsn.py` ya elimina `sslmode`/`channel_binding` (que asyncpg no
> entiende) y activa TLS.

## Correr local

```bash
doppler run -- uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# alternativa equivalente:
doppler run -- uv run python -m app.main
```

Al arrancar, se conecta a Slack por Socket Mode (`slack_socket_mode_connected`)
y queda escuchando.

## Verificar (definition of done)

1. `@mention` al bot en un canal donde esté invitado, o mándale un DM.
2. El bot responde con texto de Claude en el mismo thread.
3. El mensaje queda en Postgres:
   ```bash
   doppler run -- uv run python -c "import asyncio;
   from sqlalchemy import text; from app.db.engine import engine;
   asyncio.run((lambda: None)())"  # o conéctate con psql y haz: SELECT * FROM message;
   ```
   Más simple: con `psql "$DATABASE_URL"` ejecuta `SELECT role, text FROM message ORDER BY created_at DESC LIMIT 5;`
4. El run aparece en tu dashboard de Langfuse (observación `sebitas-reply`).

## Configuración de la Slack App

- Socket Mode: **On** (genera el `SLACK_APP_TOKEN` con scope `connections:write`).
- Event Subscriptions (sobre Socket Mode): suscribe `app_mention` y `message.im`.
- Bot Token Scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`.
- Instala la app en el workspace y obtén el `SLACK_BOT_TOKEN`.
