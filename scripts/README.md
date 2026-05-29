# scripts/

Quick standalone scripts to probe Misterr's integrations and DB **without**
spinning up the full FastAPI + Slack stack. Each script imports from `app.*`
and runs against the same APIs (Composio, Pipedream, Postgres, R2) the
production server uses, with credentials loaded from Doppler.

The point: avoid the deploy-test-fail cycle. If you suspect a bug like "does
Composio return what we expect for this query?", a 5-second script run is the
right tool. A 3-minute Render redeploy is not.

## Usage

All scripts assume Doppler is configured for the project. Run via:

```bash
doppler run -- uv run python scripts/<name>.py [args]
```

If you're not using Doppler, export the env vars manually before running.

## Available scripts

| Script | What it does |
|---|---|
| `composio_list_connections.py` | List Composio connected accounts for a user_id. Caught the singular vs plural query param bug on 2026-05-29. |
| `composio_initiate_link.py` | Mint a connect link for a toolkit. Useful to verify auth configs exist before testing in Slack. |
| `composio_get_connection.py` | Fetch a single connection by id and print its full payload (status, user_id, toolkit_slug, etc). |
| `pipedream_list_accounts.py` | List Pipedream accounts for an external_user_id. |
| `routing_decide.py` | Print which provider would be picked for a given app (no DB writes). |
| `inspect_integration_row.py` | SELECT * the integration_connection row for a (workspace, app) pair. |
| `inspect_skill_body.py` | Download a skill body from R2 and print it. Useful for finding leaked credentials in user-uploaded skills. |

## When to add a new script

If a debugging question comes up that requires hitting a provider's API or
checking DB state, and you find yourself wanting to add `print()` statements
to production code: write a script instead. Then commit it. The next time the
question recurs (and it will), the script is already there.

Scripts are NOT tests. They print, they don't assert. They're for exploration,
not for CI. Real tests go in `tests/`.
