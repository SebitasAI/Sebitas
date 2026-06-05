"use client";

import { useOrganization } from "@clerk/nextjs";

export type WorkspaceRole = "org:admin" | "org:member" | null;

/**
 * Reads the calling Clerk user's role in their active organization.
 *
 * Returns:
 *   - role: "org:admin" | "org:member" | null (null while loading or if
 *     the user has no org membership yet)
 *   - isAdmin: convenience boolean for the common gate
 *   - isLoaded: whether Clerk has resolved the org membership; gate
 *     conditional rendering on this to avoid flicker.
 *
 * Source of truth is Clerk's `useOrganization`; the backend reads the
 * same value off the signed JWT, so the two never disagree.
 */
export function useUserRole(): {
  role: WorkspaceRole;
  isAdmin: boolean;
  isLoaded: boolean;
} {
  const { membership, isLoaded } = useOrganization();
  const role = (membership?.role as WorkspaceRole) ?? null;
  return {
    role,
    isAdmin: role === "org:admin",
    isLoaded,
  };
}
