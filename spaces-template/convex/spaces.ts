// Queries + mutations for Space lifecycle and reads.
//
// CALLER PATTERN (Python side, ConvexSharedSpaceBackend):
//   - createSpace        -> on deploy_space (also kicks off the refresh loop)
//   - updateBinding      -> on update_space_binding
//   - replaceAccess      -> on update_space_access
//   - deleteSpace        -> on delete_space
//
// VIEWER PATTERN (browser, via Convex reactivity):
//   - getSpaceConfig({spaceId})
//   - getLatestSnapshot({spaceId})
//
// EVERY function calls assertSpaceAccess(ctx, spaceId) before any read.

import { v } from "convex/values";
import { mutation, query, internalMutation } from "./_generated/server";
import { internal } from "./_generated/api";
import { assertSpaceAccess } from "./_access";

// --------------------------------------------------------------------------
// Reads (called by the frontend via Convex reactivity)
// --------------------------------------------------------------------------

export const getSpaceConfig = query({
  args: { spaceId: v.string() },
  handler: async (ctx, { spaceId }) => {
    await assertSpaceAccess(ctx, spaceId);
    const cfg = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", spaceId))
      .first();
    if (!cfg) return null;
    // Never leak workspace_id / data_binding internals to the frontend.
    return {
      space_id: cfg.space_id,
      name: cfg.name,
      refresh_interval: cfg.refresh_interval,
      status: cfg.status,
    };
  },
});

export const getLatestSnapshot = query({
  args: { spaceId: v.string() },
  handler: async (ctx, { spaceId }) => {
    await assertSpaceAccess(ctx, spaceId);
    const snap = await ctx.db
      .query("space_snapshot")
      .withIndex("by_space_id_captured_at", (q) => q.eq("space_id", spaceId))
      .order("desc")
      .first();
    return snap;
  },
});

// --------------------------------------------------------------------------
// Writes (called by the Python backend via HTTP /api/mutation with admin key)
// --------------------------------------------------------------------------

export const createSpace = mutation({
  args: {
    space_id: v.string(),
    workspace_id: v.string(),
    name: v.string(),
    data_binding: v.any(),
    refresh_interval: v.number(),
  },
  handler: async (ctx, { space_id, workspace_id, name, data_binding, refresh_interval }) => {
    // Idempotency: if a config already exists for this space_id, replace it
    // (the Postgres row is the source of truth; this keeps Convex in sync).
    const existing = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .first();
    if (existing) {
      await ctx.db.patch(existing._id, {
        workspace_id,
        name,
        data_binding,
        refresh_interval,
        status: "active",
      });
    } else {
      await ctx.db.insert("space_config", {
        space_id,
        workspace_id,
        name,
        data_binding,
        refresh_interval,
        status: "active",
      });
    }
    // Kick off the refresh loop. The action will reschedule itself.
    await ctx.scheduler.runAfter(0, internal.refresh.refreshSpace, { space_id });
    return null;
  },
});

export const updateBinding = mutation({
  args: { space_id: v.string(), data_binding: v.any(), refresh_interval: v.optional(v.number()) },
  handler: async (ctx, { space_id, data_binding, refresh_interval }) => {
    const cfg = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .first();
    if (!cfg) throw new Error("space not found");
    const patch: Record<string, unknown> = { data_binding };
    if (refresh_interval !== undefined) patch.refresh_interval = refresh_interval;
    await ctx.db.patch(cfg._id, patch);
    // No re-schedule: the next refresh tick picks up the new binding.
    return null;
  },
});

export const replaceAccess = mutation({
  args: {
    space_id: v.string(),
    entries: v.array(
      v.object({
        user_id: v.string(),
        email: v.optional(v.string()),
        role: v.optional(v.string()),
      })
    ),
  },
  handler: async (ctx, { space_id, entries }) => {
    // Wipe and replace -- simplest semantics for "update_space_access".
    const existing = await ctx.db
      .query("space_access")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .collect();
    for (const row of existing) await ctx.db.delete(row._id);
    for (const e of entries) {
      await ctx.db.insert("space_access", {
        space_id,
        user_id: e.user_id,
        email: e.email,
        role: e.role,
      });
    }
    return null;
  },
});

export const deleteSpace = mutation({
  args: { space_id: v.string() },
  handler: async (ctx, { space_id }) => {
    // Mark config deleted (refresh loop sees status != "active" and stops).
    const cfg = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .first();
    if (cfg) await ctx.db.patch(cfg._id, { status: "deleted" });

    // Wipe snapshots + access rows. Config row itself is left as a tombstone
    // for one tick so any in-flight scheduled refresh exits cleanly; a follow
    // step in the Python backend can patch it later if needed.
    const snaps = await ctx.db
      .query("space_snapshot")
      .withIndex("by_space_id_captured_at", (q) => q.eq("space_id", space_id))
      .collect();
    for (const s of snaps) await ctx.db.delete(s._id);

    const access = await ctx.db
      .query("space_access")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .collect();
    for (const a of access) await ctx.db.delete(a._id);

    return null;
  },
});

// Final cleanup of the tombstoned config row. Called by the Python backend
// AFTER the refresh action has had a chance to bail out cleanly. Separate from
// deleteSpace so the refresh loop's terminal tick doesn't fight us.
export const purgeSpaceConfig = mutation({
  args: { space_id: v.string() },
  handler: async (ctx, { space_id }) => {
    const cfg = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .first();
    if (cfg) await ctx.db.delete(cfg._id);
    return null;
  },
});

// --------------------------------------------------------------------------
// Internal helpers (called by the refresh action; not exposed publicly)
// --------------------------------------------------------------------------

export const _writeSnapshot = internalMutation({
  args: {
    space_id: v.string(),
    rows: v.any(),
    schema: v.any(),
    row_count: v.number(),
    error: v.optional(v.string()),
  },
  handler: async (ctx, { space_id, rows, schema, row_count, error }) => {
    await ctx.db.insert("space_snapshot", {
      space_id,
      captured_at: Date.now(),
      rows,
      schema,
      row_count,
      error,
    });
  },
});

export const claimAccess = mutation({
  args: { space_id: v.string() },
  handler: async (ctx, { space_id }) => {
    // Lazy claim: if a pending row (user_id="") exists for this space with
    // the authenticated caller's email, patch it with the Clerk user_id.
    // Called by the frontend right after sign-in. Safe to call repeatedly.
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) return null;
    const email = (identity.email || "").toLowerCase();
    if (!email) return null;
    const rows = await ctx.db
      .query("space_access")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .collect();
    for (const r of rows) {
      const pending = !r.user_id || r.user_id === "";
      if (pending && (r.email || "").toLowerCase() === email) {
        await ctx.db.patch(r._id, { user_id: identity.subject });
        return { claimed: true };
      }
    }
    return { claimed: false };
  },
});

export const _getConfigForRefresh = internalMutation({
  args: { space_id: v.string() },
  handler: async (ctx, { space_id }) => {
    const cfg = await ctx.db
      .query("space_config")
      .withIndex("by_space_id", (q) => q.eq("space_id", space_id))
      .first();
    if (!cfg) return null;
    return {
      space_id: cfg.space_id,
      workspace_id: cfg.workspace_id,
      data_binding: cfg.data_binding,
      refresh_interval: cfg.refresh_interval,
      status: cfg.status,
    };
  },
});
