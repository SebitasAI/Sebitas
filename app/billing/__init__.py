"""Billing subsystem: plans, credit balance, ledger, Stripe glue.

Slice 1 (this slice) gives us the local data layer: tables, models,
repository, and the runner integration (pre-flight + debit). No Stripe
calls yet, no webhooks, no UI.

Pre-existing customers (Simetrik, Antiff, diio, Supersonik) are
backfilled to plan = 'unlimited' so the pre-flight check is a no-op
for them. New workspaces default to 'free'.

See `app/billing/plans.py` for the tier taxonomy and credit math.
"""
