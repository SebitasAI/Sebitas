"""FastAPI router for the internal endpoint Convex actions call to refresh
Space data via the integrations gateway.

Auth: shared secret in the `X-Internal-Token` header (constant-time compare).
Only the Convex deployment knows this token; users / agents / sandbox never
see it. Credentials of the underlying integration NEVER leave the gateway --
this endpoint returns only the result rows + schema.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from langfuse import get_client
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.context import set_run_context, workspace_id_var
from app.config import get_settings
from app.db.models import Space
from app.db.session import get_session
from app.integrations import gateway as _integrations
from app.integrations.errors import to_user_message
from app.integrations.provider import IntegrationError

log = structlog.get_logger(__name__)
_langfuse = get_client()

router = APIRouter(prefix="/internal/spaces", tags=["spaces-internal"])


class RefreshRequest(BaseModel):
    space_id: str
    workspace_id: str
    data_binding: dict[str, Any]


def _auth(token_header: str | None) -> None:
    secret = get_settings().internal_spaces_token
    if not secret:
        # Refuse to operate if the deployment didn't configure the token --
        # we don't want to silently accept unauthenticated calls.
        raise HTTPException(status_code=503, detail="internal token not configured")
    if not token_header or not hmac.compare_digest(token_header, secret):
        raise HTTPException(status_code=401, detail="bad internal token")


@router.post("/refresh")
async def refresh_space(
    payload: RefreshRequest,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, Any]:
    _auth(x_internal_token)

    # Tenant validation: the Space row in Postgres is the source of truth on
    # which workspace owns which space_id. We REJECT if the workspace_id the
    # Convex action sent doesn't match what we have. That defends against a
    # Convex deployment being misconfigured / a stale row drifting.
    try:
        space_uuid = uuid.UUID(payload.space_id)
        workspace_uuid = uuid.UUID(payload.workspace_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="space_id / workspace_id must be UUIDs")

    async with get_session() as session:
        row = (
            await session.execute(
                select(Space).where(Space.id == space_uuid)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="space not found")
    if row.workspace_id != workspace_uuid:
        log.warning(
            "space_refresh_workspace_mismatch",
            space_id=payload.space_id,
            claimed_workspace=payload.workspace_id,
            actual_workspace=str(row.workspace_id),
        )
        raise HTTPException(status_code=403, detail="workspace mismatch")

    binding = payload.data_binding or {}
    app = binding.get("app")
    action_id = binding.get("action_id")
    params = binding.get("params") or {}
    if not app or not action_id:
        return {"rows": [], "schema": [], "error": "data_binding missing app or action_id"}

    # Set the workspace context so the integrations gateway resolves the right
    # connected account. No skills context needed -- this isn't an agent run.
    set_run_context(
        workspace_id=str(row.workspace_id),
        run_id=f"space-refresh:{row.id}",
        skills_context="",
    )

    with _langfuse.start_as_current_observation(
        as_type="span",
        name=f"space-refresh:{row.name}",
        input={"space_id": payload.space_id, "app": app, "action_id": action_id},
    ) as span:
        try:
            raw = await _integrations.run_action_raw(app, action_id, params)
        except IntegrationError as e:
            msg = to_user_message(e, app)
            log.warning("space_refresh_action_failed",
                        space_id=payload.space_id, kind=e.kind, status=e.status)
            span.update(output=f"error: {e.kind}")
            return {"rows": [], "schema": [], "error": msg}
        except Exception as exc:  # noqa: BLE001
            log.warning("space_refresh_action_failed", space_id=payload.space_id, error=str(exc))
            span.update(output=f"error: {exc}")
            return {"rows": [], "schema": [], "error": str(exc)[:300]}

        # Pipedream wraps action results as {ret: <action's actual return>,
        # exports, os, ...}. We want the action's return.
        ret = raw.get("ret", raw) if isinstance(raw, dict) else raw
        rows, schema = _extract_rows(ret)
        span.update(output=f"rows={len(rows)}")

    log.info(
        "space_refresh_done",
        space_id=payload.space_id,
        workspace_id=str(row.workspace_id),
        rows=len(rows),
    )
    return {"rows": rows, "schema": schema, "error": None}


def _extract_rows(value) -> tuple[list[dict], list[dict]]:
    """Pull a (rows, schema) pair out of a provider's structured return.

    Handles three shapes seen in practice:
    - Nested data wrapper: `{data: {rows: [[...]], results_metadata: {columns: [...]}}}`
    - Plain list-of-dicts (Airtable etc.).
    - Anything else: wrap as a single `{value: ...}` row.

    The previous string-parsing version is gone -- callers feed dicts now."""
    if value is None:
        return [], []

    if isinstance(value, dict):
        data = value.get("data") if isinstance(value.get("data"), dict) else None
        if data:
            rows_raw = data.get("rows")
            cols_meta = (data.get("results_metadata") or {}).get("columns") or []
            schema = [
                {
                    "name": c.get("name") or c.get("display_name") or f"col{i}",
                    "type": c.get("base_type") or c.get("effective_type") or "unknown",
                }
                for i, c in enumerate(cols_meta) if isinstance(c, dict)
            ]
            if isinstance(rows_raw, list):
                if rows_raw and isinstance(rows_raw[0], list) and schema:
                    col_names = [s["name"] for s in schema]
                    return (
                        [dict(zip(col_names, r, strict=False)) for r in rows_raw],
                        schema,
                    )
                if rows_raw and isinstance(rows_raw[0], dict):
                    inferred = [{"name": k, "type": "string"} for k in rows_raw[0].keys()]
                    return rows_raw, schema or inferred
                return [], schema

    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            schema = [{"name": k, "type": "string"} for k in value[0].keys()]
            return value, schema
        return [{"value": value}], [{"name": "value", "type": "json"}]

    return [{"value": value}], [{"name": "value", "type": "json"}]
