class Message:
    def __init__(self, data):
        self.id = data.get('id')
        self.body = data.get('body')
        self.created_at = data.get('createdAt')
        self.source = data.get('source', {}).get('name') if data.get('source') else "Unknown"
        self.image_url = data.get('imageUrl')
        self.visitor_name = data.get('visitor', {}).get('name') if data.get('visitor') else data.get('origin', 'Unknown')
    
    def __repr__(self):
        return f"<Message {self.id}: {self.body} from {self.visitor_name} at {self.created_at}>"

