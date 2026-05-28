"""SpaceBackend interface: provisioning + lifecycle of a Space.

Same pattern as `IntegrationProvider` (slice 4a). Today there's one impl
(`MockSpaceBackend`, in-memory) so the agent surface + DB schema + tool flow
can be validated without external infra. In 4B-ii we plug a
`ConvexSharedSpaceBackend` here; the gateway / tools / agent above this layer
won't change.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SpaceDeployment:
    """Result of a deploy: refs + URL the backend produced for this Space.
    All fields nullable because different backends populate different subsets
    (Mock has no real Convex refs; future deployment-per-Space populates them all)."""

    frontend_url: str | None = None
    convex_project_ref: str | None = None
    convex_deployment_ref: str | None = None
    admin_key_vault_ref: str | None = None


class SpaceBackend(ABC):
    """One Space-provisioning backend. Mock today; Convex shared-deployment
    in 4B-ii. Methods are async and must be idempotent for retries (deploy on
    the same space_id should not duplicate, delete on a missing one is no-op)."""

    name: str

    @abstractmethod
    async def deploy(
        self,
        *,
        space_id: uuid.UUID,
        workspace_id: uuid.UUID,
        name: str,
        data_binding: dict,
        access_list: list,
    ) -> SpaceDeployment:
        """Create the Space's runtime state (Convex rows, scheduled refresh, etc.)
        and return the resulting refs / URL for the caller to persist."""

    @abstractmethod
    async def update_binding(
        self, *, space_id: uuid.UUID, data_binding: dict
    ) -> None:
        """Change what the Space queries. Must NOT re-provision."""

    @abstractmethod
    async def update_access(
        self, *, space_id: uuid.UUID, access_list: list
    ) -> None:
        """Replace the Space's access list."""

    @abstractmethod
    async def delete(self, *, space_id: uuid.UUID) -> None:
        """Tear down the Space's runtime state. Idempotent: missing space = no-op."""
