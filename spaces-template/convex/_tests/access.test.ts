// Anti-fuga tests, parte 1: data isolation by space_id.
//
// Populates two Spaces (A, B) with different data, then asserts that no
// query/mutation can be coerced into returning the other's rows. These
// tests run via `npm test` (convex-test) against an in-process Convex.
//
// 4B-iii will add parte 2 (auth/access denied) once Clerk JWT is wired.

import { convexTest } from "convex-test";
import { expect, test } from "vitest";
import schema from "../schema";
import { api, internal } from "../_generated/api";

const A = "11111111-aaaa-aaaa-aaaa-111111111111";
const B = "22222222-bbbb-bbbb-bbbb-222222222222";

async function seedTwoSpaces(t: ReturnType<typeof convexTest>) {
  await t.mutation(api.spaces.createSpace, {
    space_id: A,
    workspace_id: "wsA",
    name: "Space A",
    data_binding: { app: "metabase", action_id: "metabase-run-query", params: { cardId: 1 } },
    refresh_interval: 60,
  });
  await t.mutation(api.spaces.createSpace, {
    space_id: B,
    workspace_id: "wsB",
    name: "Space B",
    data_binding: { app: "airtable", action_id: "airtable-list-records", params: {} },
    refresh_interval: 60,
  });
  // Seed one snapshot per Space so cross-queries have something to "leak".
  await t.mutation(internal.spaces._writeSnapshot, {
    space_id: A, rows: [{ k: "A1" }], schema: [{ name: "k", type: "string" }], row_count: 1,
  });
  await t.mutation(internal.spaces._writeSnapshot, {
    space_id: B, rows: [{ k: "B1" }], schema: [{ name: "k", type: "string" }], row_count: 1,
  });
}

test("getSpaceConfig returns ONLY the requested space", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  const cfgA = await t.query(api.spaces.getSpaceConfig, { spaceId: A });
  const cfgB = await t.query(api.spaces.getSpaceConfig, { spaceId: B });
  expect(cfgA?.name).toBe("Space A");
  expect(cfgB?.name).toBe("Space B");
  // Workspace_id is never leaked to the frontend payload.
  expect((cfgA as any).workspace_id).toBeUndefined();
});

test("getLatestSnapshot does not leak rows across spaces", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  const sA = await t.query(api.spaces.getLatestSnapshot, { spaceId: A });
  const sB = await t.query(api.spaces.getLatestSnapshot, { spaceId: B });
  expect(sA?.rows).toEqual([{ k: "A1" }]);
  expect(sB?.rows).toEqual([{ k: "B1" }]);
});

test("requests for an unknown space_id throw -- no fallback to ANY row", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  await expect(
    t.query(api.spaces.getSpaceConfig, { spaceId: "ghost-space" })
  ).rejects.toThrow();
  await expect(
    t.query(api.spaces.getLatestSnapshot, { spaceId: "ghost-space" })
  ).rejects.toThrow();
});

test("deleteSpace wipes ONLY that space's rows", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  await t.mutation(api.spaces.deleteSpace, { space_id: A });
  // A is deleted: assertSpaceAccess rejects (status != "active").
  await expect(
    t.query(api.spaces.getLatestSnapshot, { spaceId: A })
  ).rejects.toThrow();
  // B is intact.
  const sB = await t.query(api.spaces.getLatestSnapshot, { spaceId: B });
  expect(sB?.rows).toEqual([{ k: "B1" }]);
});

test("updateBinding on A does not touch B", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  await t.mutation(api.spaces.updateBinding, {
    space_id: A,
    data_binding: { app: "metabase", action_id: "metabase-run-query", params: { cardId: 999 } },
  });
  const cfgB = await t.query(api.spaces.getSpaceConfig, { spaceId: B });
  expect(cfgB?.name).toBe("Space B");  // unchanged
});

test("replaceAccess on A does not affect B's access rows", async () => {
  const t = convexTest(schema);
  await seedTwoSpaces(t);
  await t.mutation(api.spaces.replaceAccess, {
    space_id: A,
    entries: [{ user_id: "user_alice", email: "alice@x.com", role: "admin" }],
  });
  await t.mutation(api.spaces.replaceAccess, {
    space_id: B,
    entries: [{ user_id: "user_bob", email: "bob@y.com", role: "admin" }],
  });
  await t.mutation(api.spaces.replaceAccess, {
    space_id: A,
    entries: [],  // wipe A's access
  });
  // B's access is intact.
  const bRows = await t.run(async (ctx) =>
    ctx.db
      .query("space_access")
      .withIndex("by_space_id", (q) => q.eq("space_id", B))
      .collect()
  );
  expect(bRows).toHaveLength(1);
  expect(bRows[0].user_id).toBe("user_bob");
});
