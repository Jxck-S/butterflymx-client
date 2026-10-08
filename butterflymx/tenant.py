from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .access import Access
from .call import Call
from .door import Door
from .message import Message

if TYPE_CHECKING:
    from .client import ButterflyMXClient

# GraphQL selections for each Tenant connection, shared by the single-purpose
# queries and the combined overview query.
DOORS_FIELDS = """
    accessPoints {
        nodes {
            id
            name
            capabilities
            online
            openDuration
            building { id name }
        }
    }
"""

MESSAGES_FIELDS = """
    messages {
        nodes {
            ... on TextMessage {
                id
                body
                createdAt
                imageUrl
                origin
                source { ... on Device { name } }
            }
        }
    }
"""

CALLS_FIELDS = """
    calls {
        nodes {
            id
            loggedAt
            displayStatus
            notificationType
            imageUrl
            device {
                ... on Intercom { name }
                ... on Device { name }
            }
        }
    }
"""

ACCESS_LOGS_FIELDS = """
    doorReleases {
        nodes {
            id
            loggedAt
            imageUrl
            type
            method
            accessPoint { name }
            device { ... on Device { name } }
        }
    }
"""


@dataclass
class TenantOverview:
    """Everything for a tenant, fetched in one request by `Tenant.get_overview()`."""

    doors: list[Door] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    access_logs: list[Access] = field(default_factory=list)


def _nodes(tenant_node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return (tenant_node.get(key) or {}).get("nodes") or []


class Tenant:
    def __init__(self, data: dict[str, Any], client: ButterflyMXClient) -> None:
        self._client = client
        self.id: str = data["id"]
        self.name: str | None = data.get("name")

    def __repr__(self) -> str:
        return f"<Tenant {self.id}: {self.name}>"

    async def _query(self, name: str, fields: str) -> dict[str, Any]:
        """Query fields on this tenant. Returns the tenant node, or {} if not found."""
        query = f"""
        query {name}($ids: [ID!]!) {{
            nodes(ids: $ids) {{
                ... on Tenant {{
                    id
                    {fields}
                }}
            }}
        }}
        """
        data = await self._client.query_graphql(query, variables={"ids": [self.id]})
        nodes = (data.get("data") or {}).get("nodes") or []
        return (nodes[0] if nodes else None) or {}

    def _doors(self, node: dict[str, Any]) -> list[Door]:
        return [Door(d, tenant_id=self.id, client=self._client) for d in _nodes(node, "accessPoints")]

    async def get_doors(self) -> list[Door]:
        """Doors (access points) this tenant can open."""
        return self._doors(await self._query("TenantAccessPoints", DOORS_FIELDS))

    async def get_door(self, door_id: str) -> Door | None:
        """A single door by ID, with fresh status, or None if not found."""
        return next((d for d in await self.get_doors() if d.id == door_id), None)

    async def get_messages(self) -> list[Message]:
        """Text messages, newest first."""
        node = await self._query("TenantMessages", MESSAGES_FIELDS)
        return [Message(d) for d in _nodes(node, "messages")]

    async def get_calls(self) -> list[Call]:
        """Intercom call history, newest first."""
        node = await self._query("TenantCalls", CALLS_FIELDS)
        return [Call(d) for d in _nodes(node, "calls")]

    async def get_access_logs(self) -> list[Access]:
        """Door releases, newest first."""
        node = await self._query("TenantDoorReleases", ACCESS_LOGS_FIELDS)
        return [Access(d) for d in _nodes(node, "doorReleases")]

    async def get_overview(self) -> TenantOverview:
        """Doors, messages, calls and access logs in a single request."""
        node = await self._query(
            "TenantOverview", DOORS_FIELDS + MESSAGES_FIELDS + CALLS_FIELDS + ACCESS_LOGS_FIELDS
        )
        return TenantOverview(
            doors=self._doors(node),
            messages=[Message(d) for d in _nodes(node, "messages")],
            calls=[Call(d) for d in _nodes(node, "calls")],
            access_logs=[Access(d) for d in _nodes(node, "doorReleases")],
        )
