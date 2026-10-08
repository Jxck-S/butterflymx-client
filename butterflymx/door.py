from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .exceptions import ButterflyMXApiError

if TYPE_CHECKING:
    from .client import ButterflyMXClient

_LOGGER = logging.getLogger(__name__)


class Door:
    def __init__(self, data: dict[str, Any], tenant_id: str, client: ButterflyMXClient) -> None:
        self._client = client
        self.tenant_id = tenant_id
        self._apply(data)

    def _apply(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.name: str | None = data.get("name")
        self.online: bool | None = data.get("online")
        self.open_duration: int | None = data.get("openDuration")
        building = data.get("building")
        self.building_name: str = building.get("name") if building else "Unknown"

    def __repr__(self) -> str:
        status = "Online" if self.online else "Offline"
        return f"<Door {self.id}: {self.name} ({self.building_name}) - {status}>"

    async def open(self) -> None:
        """Unlock the door.

        Raises:
            ButterflyMXAuthError, ButterflyMXConnectionError, ButterflyMXApiError: the unlock failed.
        """
        _LOGGER.info("Opening door %s (%s)", self.name, self.id)
        await self._client._authed_post(
            self._client.UNLOCK_URL,
            {"accessPointId": self.id, "source": "mobile_app", "tenantId": self.tenant_id},
        )

    async def update(self) -> None:
        """Refetch this door's details (e.g. `online`) from the API.

        Raises:
            ButterflyMXApiError: the door no longer exists for this tenant.
        """
        from .tenant import Tenant

        fresh = await Tenant({"id": self.tenant_id}, client=self._client).get_door(self.id)
        if fresh is None:
            raise ButterflyMXApiError(f"Door {self.id} not found for tenant {self.tenant_id}")
        self.name, self.online = fresh.name, fresh.online
        self.open_duration, self.building_name = fresh.open_duration, fresh.building_name
