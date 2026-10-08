from __future__ import annotations

from typing import Any


def _name(obj: dict[str, Any] | None) -> str:
    return (obj or {}).get("name") or "Unknown"


class Access:
    """A door release (someone opened a door)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data.get("id")
        self.logged_at: str | None = data.get("loggedAt")
        self.image_url: str | None = data.get("imageUrl")
        self.type: str | None = data.get("type")  # e.g. VISITOR, TENANT
        self.method: str | None = data.get("method")  # e.g. SWIPE_TO_OPEN
        self.door_name: str = _name(data.get("accessPoint"))
        self.device_name: str = _name(data.get("device"))

    def __repr__(self) -> str:
        return f"<Access {self.id}: {self.type} via {self.method} at {self.logged_at} on {self.door_name}>"
