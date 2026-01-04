import json
from .door import Door
from .message import Message
from .call import Call

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
        print(f"Fetching Doors (Access Points) for Tenant {self.id}...")
        data = await self._client.query_graphql(query, variables={"ids": [self.id]})
        
        if data and 'data' in data and 'nodes' in data['data']:
            tenant_node = data['data']['nodes'][0]
            if tenant_node and 'accessPoints' in tenant_node:
                return [Door(d, tenant_id=self.id, client=self._client) for d in tenant_node['accessPoints']['nodes']]
        return []

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
        print(f"Fetching Messages for Tenant {self.id}...")
        data = await self._client.query_graphql(query, variables={"tenantIds": [self.id]})
        
        messages = []
        if data and 'data' in data and 'nodes' in data['data']:
            tenant_node = data['data']['nodes'][0]
            if tenant_node and 'messages' in tenant_node:
                for msg_data in tenant_node['messages']['nodes']:
                    messages.append(Message(msg_data))
        return messages

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
                  visitor {
                    name
                  }
                }
              }
            }
          }
        }
        """
        print(f"Fetching Calls for Tenant {self.id}...")
        data = await self._client.query_graphql(query, variables={"tenantIds": [self.id]})
        
        calls = []
        if data and 'data' in data and 'nodes' in data['data']:
            tenant_node = data['data']['nodes'][0]
            if tenant_node and 'calls' in tenant_node:
                for call_data in tenant_node['calls']['nodes']:
                    calls.append(Call(call_data))
        return calls
