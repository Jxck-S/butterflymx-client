import logging

_LOGGER = logging.getLogger(__name__)


class Door:
    def __init__(self, data, tenant_id, client):
        self._client = client
        self.tenant_id = tenant_id
        self.id = data.get('id')
        self.name = data.get('name')
        self.online = data.get('online')
        self.open_duration = data.get('openDuration')
        self.building_name = data.get('building', {}).get('name') if data.get('building') else "Unknown"

    def __repr__(self):
        status = "Online" if self.online else "Offline"
        return f"<Door {self.id}: {self.name} ({self.building_name}) - {status}>"

    async def open(self):
        _LOGGER.info("Opening door %s (%s)", self.name, self.id)
        payload = {
            "accessPointId": self.id,
            "source": "mobile_app",
            "tenantId": self.tenant_id
        }
        status, text = await self._client.authed_post(self._client.UNLOCK_URL, payload)
        if status in [200, 204]:
            return True
        _LOGGER.error("Failed to open door %s: %s - %s", self.name, status, text)
        return False
