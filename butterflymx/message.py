from __future__ import annotations

from typing import Any

from .access import _name


class Message:
    """A text message left at the intercom."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.body: str | None = data.get("body")
        self.created_at: str | None = data.get("createdAt")
        self.source: str = _name(data.get("source"))
        self.image_url: str | None = data.get("imageUrl")
        visitor = data.get("visitor")
        self.visitor_name: str = visitor.get("name") if visitor else data.get("origin") or "Unknown"

    def __repr__(self) -> str:
        return f"<Message {self.id}: {self.body} from {self.visitor_name} at {self.created_at}>"
