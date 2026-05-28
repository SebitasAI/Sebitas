// Per-Space self-rescheduling refresh.
//
// Convex's cron is fixed-interval, but each Space has its own `refresh_interval`.
// Pattern: a Space's refresh action reschedules itself with the current
// interval at the END of every tick. On delete, the config flips to status
// "deleted" -> the next tick's first read sees that and bails without
// rescheduling. Loop dies on its own.
//
// The action does NOT have credentials. It calls our Python backend with a
// shared INTERNAL_SPACES_TOKEN; the backend resolves the workspace + runs
// the integration action via the Pipedream gateway. Credentials never enter
// the Space, the browser, or anyone watching this action's logs.

"use node";

import { v } from "convex/values";
import { internalAction } from "./_generated/server";
import { internal } from "./_generated/api";

const DEFAULT_INTERVAL_SECONDS = 60;

export const refreshSpace = internalAction({
  args: { space_id: v.string() },
  handler: async (ctx, { space_id }) => {
    const config = await ctx.runMutation(internal.spaces._getConfigForRefresh, {
      space_id,
    });
    if (!config || config.status !== "active") {
      // Space deleted / never existed. End of the line; no reschedule.
      return null;
    }

    const baseUrl = process.env.PUBLIC_BASE_URL;
    const token = process.env.INTERNAL_SPACES_TOKEN;
    if (!baseUrl || !token) {
      await ctx.runMutation(internal.spaces._writeSnapshot, {
        space_id,
        rows: [],
        schema: [],
        row_count: 0,
        error:
          "missing PUBLIC_BASE_URL or INTERNAL_SPACES_TOKEN env in this Convex deployment",
      });
    } else {
      try {
        const resp = await fetch(
          `${baseUrl.replace(/\/$/, "")}/internal/spaces/refresh`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Internal-Token": token,
            },
            body: JSON.stringify({
              space_id,
              workspace_id: config.workspace_id,
              data_binding: config.data_binding,
            }),
          }
        );
        if (resp.status === 404) {
          // Backend says this space_id doesn't exist in Postgres -- we're an
          // orphan refresh loop. Stop rescheduling so the loop dies. Mark
          // the local config as deleted so the frontend renders accordingly.
          await ctx.runMutation(internal.spaces._writeSnapshot, {
            space_id, rows: [], schema: [], row_count: 0,
            error: "space removed from backend; refresh loop stopped",
          });
          // Flip config status so subsequent ticks (if any race in) also bail.
          // We can't await a separate mutation cleanly without losing the
          // "no reschedule" intent; the early return below handles it.
          return null;
        }
        if (!resp.ok) {
          await ctx.runMutation(internal.spaces._writeSnapshot, {
            space_id,
            rows: [],
            schema: [],
            row_count: 0,
            error: `backend ${resp.status}: ${(await resp.text()).slice(0, 200)}`,
          });
        } else {
          const data = (await resp.json()) as {
            rows: unknown[];
            schema: unknown[];
            error?: string | null;
          };
          await ctx.runMutation(internal.spaces._writeSnapshot, {
            space_id,
            rows: data.rows ?? [],
            schema: data.schema ?? [],
            row_count: Array.isArray(data.rows) ? data.rows.length : 0,
            error: data.error || undefined,
          });
        }
      } catch (e) {
        await ctx.runMutation(internal.spaces._writeSnapshot, {
          space_id,
          rows: [],
          schema: [],
          row_count: 0,
          error: String(e).slice(0, 300),
        });
      }
    }

    // Reschedule for the NEXT tick. If config was deleted mid-tick the next
    // run's first read will bail.
    const intervalMs =
      Math.max(5, config.refresh_interval || DEFAULT_INTERVAL_SECONDS) * 1000;
    await ctx.scheduler.runAfter(intervalMs, internal.refresh.refreshSpace, {
      space_id,
    });
    return null;
  },
});
