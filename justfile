# Recipes for the Misterr monorepo.
#
# Run `just <recipe>` from repo root. Backend Python lives in `app/`; the two
# Next.js apps (marketing + dashboard) live in `website/` and `web/` and are
# managed via pnpm workspaces.

# ----- frontend ---------------------------------------------------------- #

# Install deps for both Next.js apps (one pnpm install at the workspace root).
install-frontend:
    pnpm install

# Marketing site (misterr.ai). Static export, deploys to Cloudflare Pages.
website-dev:
    cd website && pnpm dev

website-build:
    cd website && pnpm build

website-lint:
    cd website && pnpm lint

# Dashboard (app.misterr.ai). SSR + Clerk, deploys to Render.
web-dev:
    cd web && pnpm dev

web-build:
    cd web && pnpm build

web-lint:
    cd web && pnpm lint
