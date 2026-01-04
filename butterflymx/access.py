class Access:
    def __init__(self, data):
        self.id = data.get('id')
        self.logged_at = data.get('loggedAt')
        self.image_url = data.get('imageUrl')
        self.type = data.get('type')  # e.g., VISITOR, TENANT
        self.method = data.get('method')  # e.g., SWIPE_TO_OPEN
        self.door_name = data.get('accessPoint', {}).get('name') if data.get('accessPoint') else "Unknown"
        self.device_name = data.get('device', {}).get('name') if data.get('device') else "Unknown"

    def __repr__(self):
        return f"<Access {self.id}: {self.type} via {self.method} at {self.logged_at} on {self.door_name}>"
