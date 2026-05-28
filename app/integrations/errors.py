"""Map IntegrationError to a user-facing Spanish message (Slack mrkdwn).

These strings are the agent's contract with the user when integration calls
fail: actionable, never echoing raw provider JSON. The model sees the same
text the user does, so it can also adapt next steps.

Connector-limitation detection is heuristic (substring match on the error
body). It will improve as we see real responses; for now it covers the
session-only pattern (Metabase) and similar shapes."""

from __future__ import annotations

from app.integrations.provider import IntegrationError


_SESSION_HINTS = (
    "session",
    "cookie",
    "x-metabase-session",
    "only supports session",
    "session-based",
)


def _connector_limitation_hint(body: str) -> str | None:
    """Return a hint string if the response body suggests the connector itself
    rejects the auth shape (vs the user's credentials being wrong)."""
    if not body:
        return None
    b = body.lower()
    if any(h in b for h in _SESSION_HINTS):
        return "auth de sesión (cookie) en lugar de API key"
    return None


def _trim(detail) -> str:
    if detail is None:
        return ""
    s = str(detail)
    return s[:240].rstrip() + "…" if len(s) > 240 else s


def to_user_message(err: IntegrationError, app: str) -> str:
    kind, status, detail = err.kind, err.status, err.detail

    # Pre-call kinds (validation step before invoking the provider).
    if kind == "auth_missing_fields" and isinstance(detail, list):
        fields = ", ".join(f"`{f}`" for f in detail) or "campos requeridos"
        return (
            f"La conexión a *{app}* está incompleta: falta(n) {fields}. "
            f"Reconectá pegando el/los valor(es) (decime: «reconectá {app}»)."
        )
    if kind == "auth_expired":
        return f"La conexión a *{app}* expiró. Reconectala con «reconectá {app}»."
    if kind == "account_not_found":
        return (
            f"No encuentro la cuenta conectada de *{app}* en el proveedor. "
            f"Reconectala con «reconectá {app}»."
        )

    # Connector-limitation heuristic. Only fires when the status is in the
    # auth-rejection spectrum (or kind explicitly set), so a 422/429/5xx whose
    # body happens to mention "session" doesn't get misclassified.
    body_str = str(detail) if detail else ""
    plausible_auth_reject = status in (400, 401, 403)
    hint = _connector_limitation_hint(body_str) if plausible_auth_reject else None
    if hint or kind == "connector_limitation":
        return (
            f"El connector de *{app}* tiene una limitación: rechazó el formato "
            f"de auth (parece pedir {hint or 'otro shape de credenciales'}). "
            f"Esto es del connector, no del agente. Workarounds: usar las "
            f"credenciales en el formato esperado, o esperar a que metamos "
            f"fallback (HTTP directo / MCP) para *{app}*."
        )

    if kind == "auth_failed" or status == 401:
        return (
            f"La conexión a *{app}* está rechazando la autenticación. "
            f"Causas comunes: credenciales inválidas, falta un campo, o el "
            f"connector espera otro tipo de auth (por ejemplo session-only vs "
            f"API key). Reconectá la cuenta o verificá los campos."
        )
    if kind == "permission_denied" or status == 403:
        return (
            f"La conexión a *{app}* no tiene permisos para esta operación. "
            f"Revisá el rol/grupo del usuario en {app} o el scope de la credencial."
        )
    if kind == "not_found" or status == 404:
        return (
            f"La action o cuenta de *{app}* no existe / no está disponible. "
            f"Verificá el id con `find_actions`."
        )
    if kind == "validation" or status == 422:
        d = _trim(detail) or "parámetros inválidos"
        return (
            f"Parámetros inválidos para *{app}*: {d}. Revisá los campos "
            f"requeridos con `find_actions`."
        )
    if kind == "rate_limited" or status == 429:
        return f"Rate limit alcanzado en *{app}*. Esperá unos segundos y reintentá."
    if kind == "network":
        return f"No pude alcanzar *{app}* (timeout o red). Reintentá."
    if status and status >= 500:
        return f"*{app}* respondió con un error temporal ({status}). Reintentá en un momento."

    d = _trim(detail)
    if status and d:
        return f"*{app}* respondió con error {status}: {d}"
    if status:
        return f"*{app}* respondió con error {status}."
    return f"Error en *{app}*: {kind}."
