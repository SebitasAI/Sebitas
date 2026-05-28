// Tenant guard. EVERY query/mutation that touches space_* tables must call
// `assertSpaceAccess(ctx, spaceId)` BEFORE reading or writing.
//
// Two-stage identity check (4B-iii):
// 1. Space exists + status="active" (structural invariant).
// 2. Caller is authenticated via Clerk AND is listed in space_access either:
//    (a) by user_id (resolved-at-deploy path, the common case), OR
//    (b) by email when their access row is still "pending" (user_id="" because
//        they weren't registered in Clerk at deploy time -- lazy claim).
//
// Failure modes (each throws a distinct message for debuggability):
//   - "space not found or not active"
//   - "not authenticated"
//   - "forbidden: not in access list"
//
// Note: queries cannot write. The lazy claim (b) ACCEPTS the read but doesn't
// patch the row -- patching happens in `_claimAccess` mutation, called by the
// frontend after successful sign-in (the App component triggers it).

import { GenericMutationCtx, GenericQueryCtx } from "convex/server";
import { DataModel } from "./_generated/dataModel";

type AnyCtx = GenericQueryCtx<DataModel> | GenericMutationCtx<DataModel>;

export async function assertSpaceAccess(ctx: AnyCtx, spaceId: string): Promise<void> {
  if (!spaceId || typeof spaceId !== "string") {
    throw new Error("space_id required");
  }

  const config = await ctx.db
    .query("space_config")
    .withIndex("by_space_id", (q) => q.eq("space_id", spaceId))
    .first();
  if (!config || config.status !== "active") {
    throw new Error("space not found or not active");
  }

  const identity = await ctx.auth.getUserIdentity();
  if (!identity) {
    throw new Error("not authenticated");
  }
  const clerkUserId = identity.subject; // Clerk's user_id (e.g. "user_abc...")
  const email = (identity.email || "").toLowerCase() || null;

  // (a) Direct match by user_id (resolved at deploy time).
  const byId = await ctx.db
    .query("space_access")
    .withIndex("by_space_id_user_id", (q) =>
      q.eq("space_id", spaceId).eq("user_id", clerkUserId)
    )
    .first();
  if (byId) return;

  // (b) Lazy fallback: pending row with this email, awaiting first login.
  if (email) {
    const pending = await ctx.db
      .query("space_access")
      .withIndex("by_space_id", (q) => q.eq("space_id", spaceId))
      .collect();
    const match = pending.find(
      (r) => (r.user_id === "" || r.user_id === null) && (r.email || "").toLowerCase() === email
    );
    if (match) return;
  }

  throw new Error("forbidden: not in access list");
}
