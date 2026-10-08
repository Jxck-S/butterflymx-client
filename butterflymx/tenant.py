from .access import Access
from .call import Call
from .door import Door
from .message import Message


def _tenant_node(data):
    """Return the tenant node from a `nodes(ids: [...])` response, or {}."""
    nodes = ((data or {}).get('data') or {}).get('nodes') or []
    return (nodes[0] if nodes else None) or {}


class Tenant:
    def __init__(self, data, client):
        self._client = client
        self.id = data.get('id')
        self.name = data.get('name')

    def __repr__(self):
        return f"<Tenant {self.id}: {self.name}>"

    async def get_doors(self):
        query = """
        query TenantAccessPoints($ids: [ID!]!) {
            nodes(ids: $ids) {
                ... on Tenant {
                    id
                    accessPoints {
                        nodes {
                            id
                            name
                            capabilities
                            online
                            openDuration
                            building {
                                id
                                name
                            }
                        }
                    }
                }
            }
        }
        """
        data = await self._client.query_graphql(query, variables={"ids": [self.id]})

        nodes = (_tenant_node(data).get('accessPoints') or {}).get('nodes') or []
        return [Door(d, tenant_id=self.id, client=self._client) for d in nodes]

    async def get_messages(self):
        query = """
        query TenantMessages($tenantIds: [ID!]!) {
          nodes(ids: $tenantIds) {
            ... on Tenant {
              messages {
                nodes {
                  ... on TextMessage {
                    id
                    body
                    createdAt
                    imageUrl
                    origin
                    source {

                      ... on Device {
                        name
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = await self._client.query_graphql(query, variables={"tenantIds": [self.id]})

        nodes = (_tenant_node(data).get('messages') or {}).get('nodes') or []
        return [Message(d) for d in nodes]

    async def get_calls(self):
        query = """
        query TenantCalls($tenantIds: [ID!]!) {
          nodes(ids: $tenantIds) {
            ... on Tenant {
              calls {
                nodes {
                  id
                  loggedAt
                  displayStatus
                  notificationType
                  imageUrl
                  device {
                    ... on Intercom {
                        name
                    }
                    ... on Device {
                        name
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = await self._client.query_graphql(query, variables={"tenantIds": [self.id]})

        nodes = (_tenant_node(data).get('calls') or {}).get('nodes') or []
        return [Call(d) for d in nodes]

    async def get_access_logs(self):
        query = """
        query TenantDoorReleases($tenantIds: [ID!]!) {
          nodes(ids: $tenantIds) {
            ... on Tenant {
              doorReleases {
                nodes {
                  id
                  loggedAt
                  imageUrl
                  type
                  method
                  accessPoint {
                    name
                  }
                  device {
                    ... on Device {
                      name
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = await self._client.query_graphql(query, variables={"tenantIds": [self.id]})

        nodes = (_tenant_node(data).get('doorReleases') or {}).get('nodes') or []
        return [Access(d) for d in nodes]
