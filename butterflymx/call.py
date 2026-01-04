class Call:
    def __init__(self, data):
        self.id = data.get('id')
        self.logged_at = data.get('loggedAt')
        self.status = data.get('displayStatus')
        self.type = data.get('notificationType')
        self.device = data.get('device', {}).get('name') if data.get('device') else "Unknown"
        self.image_url = data.get('imageUrl')
        # User requested image_url and time/date (timestamp) are attached to both
        # logged_at is already here.


    def __repr__(self):
        return f"<Call {self.id}: {self.status} at {self.logged_at} from {self.device}>"
