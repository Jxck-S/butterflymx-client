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
        import aiohttp
        print(f"Attempting to open door {self.name} ({self.id})...")
        unlock_url = "https://api.unlock.prod.butterflymx.com/v1/access-point"
        payload = {
            "accessPointId": self.id,
            "source": "mobile_app",
            "tenantId": self.tenant_id
        }
        headers = self._client.get_headers()
        
        async with aiohttp.ClientSession() as session:
            async with session.post(unlock_url, json=payload, headers=headers) as resp:
                if resp.status in [200, 204]:
                    print("Door opened successfully!")
                    return True
                else:
                    text = await resp.text()
                    print(f"Failed to open door: {resp.status} - {text}")
                    return False
