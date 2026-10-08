from __future__ import annotations

from typing import Any

from .access import _name


class Call:
    """An intercom call."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.logged_at: str | None = data.get("loggedAt")
        self.status: str | None = data.get("displayStatus")  # e.g. OPENED_DOOR, MISSED
        self.type: str | None = data.get("notificationType")  # e.g. VISITOR
        self.device: str = _name(data.get("device"))
        self.image_url: str | None = data.get("imageUrl")

    def __repr__(self) -> str:
        return f"<Call {self.id}: {self.status} at {self.logged_at} from {self.device}>"
