# Misterr Spaces — Fixed Template

Single deploy serves every Space in the platform. Multi-tenant by `space_id` (logical isolation; physical isolation = future slice when Convex Components / Management API land).

## Architecture

- **`convex/schema.ts`** — three tables: `space_config`, `space_snapshot`, `space_access`. Every row carries `space_id`; every read goes through `assertSpaceAccess(ctx, spaceId)` in `_access.ts`.
- **`convex/spaces.ts`** — reads (used by the frontend) and writes (called by the Python `ConvexSharedSpaceBackend` over HTTP).
- **`convex/refresh.ts`** — `internalAction` that calls our Python backend `/internal/spaces/refresh`, writes a snapshot, reschedules itself by `refresh_interval`. Self-rescheduling pattern: no global cron.
- **`convex/_tests/access.test.ts`** — anti-fuga tests (parte 1): two spaces, assert no cross-leak.
- **`src/`** — Vite + React + Convex client. Renders `Live Space: {name}` + the latest snapshot table reactively. URL form: `?space=<uuid>` or `/s/<uuid>`.

## One-time setup (manual)

The platform team does this ONCE per Convex deployment:

```bash
cd spaces-template
npm install
npx convex login           # interactive
npx convex deploy          # creates the deployment if needed; prints CONVEX_URL
```

Convex Hosting serves `dist/` automatically when you run `npm run deploy`.

## Env vars (set in this Convex deployment's dashboard, NOT in Doppler)

The refresh action reads these from `process.env`:

- `PUBLIC_BASE_URL` — public URL of the Misterr Python backend (e.g. cloudflared tunnel).
- `INTERNAL_SPACES_TOKEN` — shared secret to authenticate the action's call to `/internal/spaces/refresh`. MUST match the one in Doppler on the Python side.

(Vite reads `VITE_CONVEX_URL` from `.env`/build env to know which Convex to talk to.)

## Anti-fuga (data isolation) tests

```bash
npm test
```

Covers: cross-space reads can't leak rows, deleting A leaves B intact, replacing A's access doesn't touch B, ghost `space_id` rejects.

Parte 2 (auth/access denied) lands in 4B-iii together with Clerk.

## Why a single deployment

We can't programmatically create Convex projects today (no public Management API). Single shared deployment + logical isolation is the workable path; the `SpaceBackend` interface lives on the Python side so when deployment-per-Space becomes feasible, we swap impls without touching gateway / tools / agent.
