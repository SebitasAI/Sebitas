# Auth + Clerk Organizations (slice T-5)

## What this slice adds

Slack workspace <-> Clerk Organization 1:1 mapping. Every installed
Slack workspace has a corresponding Clerk Org; team membership is
managed via Clerk (invite, remove, roles) and synced against the Slack
roster on demand.

- Backend resolves the calling user via `workspace.clerk_org_id` +
  `app_user.clerk_user_id` instead of the legacy email match.
- New endpoints under `/api/team/*` for member management.
- One-shot backfill provisions Clerk orgs for the 4 pre-existing
  workspaces (owner = oldest AppUser).

## Required Clerk dashboard config

### 1. JWT Template "backend"

Update the existing JWT template (created earlier for the email claim).
Add the org-context claims. The full template body should be:

```json
{
  "email": "{{user.primary_email_address}}",
  "org_id": "{{org.id}}",
  "org_role": "{{org.role}}",
  "org_slug": "{{org.slug}}"
}
```

Without this, the backend's `verify_clerk_jwt` won't find `org_id` in
the token and will fall back to legacy email matching. Frontend keeps
calling `getToken({ template: "backend" })`.

### 2. Enable Organizations

Dashboard -> Organizations -> Enabled. (Already enabled if you've been
using Clerk Orgs in any project.)

### 3. Frontend ClerkProvider

`app/layout.tsx` should wrap with `<ClerkProvider>` (already in place).
For the `<OrganizationSwitcher />` component to show, ensure the
provider is configured for organizations:

```tsx
<ClerkProvider organizationProfileMode="navigation">
  ...
</ClerkProvider>
```

## How the auth flow works

1. User signs in via Clerk on the web app.
2. Frontend calls `getToken({ template: "backend" })`.
3. Token carries `sub` (Clerk user id), `email`, `org_id`, `org_role`,
   `org_slug`.
4. Backend `require_app_user`:
   - Verifies JWT via JWKS.
   - If `org_id` present: looks up `Workspace.clerk_org_id == org_id`,
     finds `AppUser` by `(workspace_id, clerk_user_id == sub)`. Lazy
     links via SlackUser email if the AppUser doesn't have
     clerk_user_id set yet.
   - If `org_id` missing: legacy email match (transition fallback). Log
     event `auth_resolve_via_email_fallback` so we can audit and remove
     this path later.

## How provisioning works

### New Slack installs

1. Slack OAuth completes -> `MisterrInstallationStore.async_save`.
2. Workspace row upserted, bot token saved.
3. Scheduled tasks seeded.
4. `provision_for_installer(workspace_id, installer_slack_user_id)`:
   - Look up the installer's email via SlackUser.
   - Find Clerk user by email.
   - If found: create Clerk org with installer as `org:admin`. Save
     `workspace.clerk_org_id`.
   - If not found: log `clerk_org_deferred_no_clerk_user`. The web side
     picks it up on first login via `POST /api/team/provision`.

### Existing workspaces (backfill)

On every backend startup the lifespan calls
`provision_and_backfill_all_workspaces()`:

- For each installed workspace without `clerk_org_id`:
  - Pick the oldest AppUser as owner.
  - Resolve their email via SlackUser, find Clerk user, create org.
- For each AppUser without `clerk_user_id` in a provisioned workspace:
  - Resolve email, find Clerk user, add as `org:member`, set
    `app_user.clerk_user_id`.

Idempotent: subsequent runs hit only the rows that aren't linked yet.

### Web-first provisioning

`POST /api/team/provision` (any signed-in Clerk user):

- Reads the caller's email from the Clerk session.
- Finds every workspace where their email appears in the SlackUser
  cache.
- Provisions any workspace that's still unprovisioned, with this user
  as owner.

Frontend calls this on first login to handle the case where the user
signs up before any provisioning has happened for their workspace.

## Endpoints (all `/api/team/*` require `require_app_user`)

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/members` | any org member | Returns members with their Slack identity merged in |
| POST | `/invite` | `org:admin` | Sends a Clerk-hosted invite email |
| DELETE | `/members/{clerk_user_id}` | `org:admin` | Cannot self-remove; clears local AppUser.clerk_user_id |
| POST | `/sync-slack` | `org:admin` | mode=preview/apply. Removes Clerk members Slack marks deleted |
| POST | `/provision` | any Clerk user | Bootstrap. Walks SlackUser cache for matching email |

## Removing the legacy fallback

Once `auth_resolve_via_email_fallback` events drop to zero in
structlog for a few days (i.e. every active session has been refreshed
with the new JWT template), delete:

- `_candidate_app_users_for_email` and the legacy block in
  `require_app_user` (`app/auth/clerk.py`).
- The `/api/web/workspaces` endpoint (`app/web_api.py`) once the
  frontend stops calling it.

Until then both paths coexist so a user with a stale session doesn't
get locked out.
