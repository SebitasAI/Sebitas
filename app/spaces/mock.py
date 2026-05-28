"""In-memory MockSpaceBackend. Validates the agent flow + DB schema + tool
surface without touching Convex. In 4B-ii we swap to ConvexSharedSpaceBackend
behind the same interface; gateway / tools / agent above this layer don't
change.

State is process-local. Across process restarts the mock 'forgets'; this is
fine for the 4B-i DoD (deploy/list/update/delete via the agent in one session).
"""

from __future__ import annotations

import uuid

import structlog

from app.spaces.backend import SpaceBackend, SpaceDeployment

log = structlog.get_logger(__name__)


class MockSpaceBackend(SpaceBackend):
    name = "mock"

    def __init__(self) -> None:
        self._state: dict[uuid.UUID, dict] = {}

    async def deploy(
        self,
        *,
        space_id: uuid.UUID,
        workspace_id: uuid.UUID,
        name: str,
        data_binding: dict,
        access_list: list,
    ) -> SpaceDeployment:
        self._state[space_id] = {
            "workspace_id": str(workspace_id),
            "name": name,
            "data_binding": dict(data_binding),
            "access_list": list(access_list),
        }
        log.info(
            "mock_space_deploy", space_id=str(space_id),
            workspace_id=str(workspace_id), name=name,
            n_access=len(access_list),
        )
        return SpaceDeployment(
            frontend_url=f"http://mock-spaces.local/s/{space_id}",
            convex_project_ref="mock-project",
            convex_deployment_ref=f"mock-deploy-{space_id}",
            admin_key_vault_ref=None,
        )

    async def update_binding(self, *, space_id: uuid.UUID, data_binding: dict) -> None:
        if space_id not in self._state:
            # Idempotency: pretend it worked. The Postgres row is the source of
            # truth; if the mock lost its entry (restart) we don't fail the agent.
            log.warning("mock_space_update_binding_unknown", space_id=str(space_id))
            return
        self._state[space_id]["data_binding"] = dict(data_binding)
        log.info(
            "mock_space_update_binding", space_id=str(space_id),
            keys=list(data_binding.keys()),
        )

    async def update_access(self, *, space_id: uuid.UUID, access_list: list) -> None:
        if space_id not in self._state:
            log.warning("mock_space_update_access_unknown", space_id=str(space_id))
            return
        self._state[space_id]["access_list"] = list(access_list)
        log.info(
            "mock_space_update_access", space_id=str(space_id),
            n_access=len(access_list),
        )

    async def delete(self, *, space_id: uuid.UUID) -> None:
        existed = self._state.pop(space_id, None) is not None
        log.info("mock_space_delete", space_id=str(space_id), existed=existed)
