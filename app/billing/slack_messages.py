"""Helpers that render the user-facing Slack messages billing posts.

Single place so the runner pre-flight, the Slice 2 webhook handler
(e.g. payment_failed alerting) and any future billing-driven Slack
notifications all share the same look + URL builder."""

from __future__ import annotations

from app.config import get_settings


def _billing_url() -> str:
    """Public URL to the workspace's billing / settings page."""
    base = get_settings().web_base_url.rstrip("/")
    return f"{base}/settings/billing"


def out_of_credits_message(plan: str, balance_credits: float) -> dict:
    """Slack chat.postMessage payload (minus channel + thread_ts) for the
    hard-stop UX when a workspace runs out of credits.

    The customer sees a short message + an "Upgrade plan" button that
    opens the web billing page (Clerk auth there resolves the workspace
    on its own).

    `text` is the fallback shown in notifications and on clients that
    can't render blocks; the same content is also surfaced through
    blocks so it never reads as "[empty message]"."""
    balance_human = max(0, int(balance_credits))
    if plan == "free":
        headline = (
            "Tu workspace usó todos los créditos del plan Free de este mes. "
            "Para seguir hablando con Misterr ahora, hace upgrade a Starter."
        )
    else:
        headline = (
            f"Tu workspace usó todos los créditos del plan *{plan.title()}* "
            "de este ciclo. Renová tu plan o subí a uno con más créditos "
            "para que Misterr siga respondiendo."
        )
    body = (
        f"{headline}\n\n"
        f"Créditos disponibles: *{balance_human}*. "
        "Tu admin puede ajustar el plan desde el dashboard."
    )
    return {
        "text": body,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Ver billing"},
                        "url": _billing_url(),
                        # `value` + `action_id` make the button uniquely
                        # identifiable so handlers.py can no-op on click
                        # (URL buttons still fire `block_actions`).
                        "action_id": "billing_open_settings",
                        "value": "billing_open_settings",
                    }
                ],
            },
        ],
    }
