// Convex schema for the shared multi-tenant Spaces deployment.
//
// Every row carries `space_id`. EVERY query/mutation must filter by it AND
// pass through `assertSpaceAccess(ctx, spaceId)` -- the helper that enforces
// (a) Clerk auth + (b) presence in space_access (4B-iii adds the real Clerk
// validation; today the guard validates the structural invariant).
//
// Anti-fuga tests in `_tests/` populate two space_ids and assert that no
// query can be coerced into returning the other's rows.

import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // One row per Space. Source of truth on Convex side; mirrors the Postgres
  // `space` row (Python backend writes to both in `ConvexSharedSpaceBackend`).
  space_config: defineTable({
    space_id: v.string(),
    workspace_id: v.string(),
    name: v.string(),
    data_binding: v.any(), // { app, action_id, params?, refresh_interval? }
    refresh_interval: v.number(), // seconds
    status: v.string(), // "active" | "deleted"
  }).index("by_space_id", ["space_id"]),

  // Snapshots of refreshed data. Latest one is what the viewer sees.
  space_snapshot: defineTable({
    space_id: v.string(),
    captured_at: v.number(), // ms epoch
    rows: v.any(), // array of objects
    schema: v.any(), // array of { name, type }
    row_count: v.number(),
    error: v.optional(v.string()),
  })
    .index("by_space_id_captured_at", ["space_id", "captured_at"]),

  // Access list. user_id is Clerk's user id (string). 4B-iii enforces it.
  space_access: defineTable({
    space_id: v.string(),
    user_id: v.string(),
    email: v.optional(v.string()),
    role: v.optional(v.string()), // "admin" | "viewer"
  })
    .index("by_space_id", ["space_id"])
    .index("by_space_id_user_id", ["space_id", "user_id"]),
});
