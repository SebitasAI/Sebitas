"""Spaces: live, isolated read-only dashboards backed by the integrations
gateway. Single template, parametrized by config (data_binding + access_list).

Sub-slice 4B-i: foundation. Agent tools + DB schema + SpaceBackend interface
+ in-memory MockSpaceBackend. No external infra yet. Convex template +
real backend land in 4B-ii; Clerk auth lands in 4B-iii.
"""
