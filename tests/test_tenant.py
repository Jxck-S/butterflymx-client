from butterflymx import Access, Call, Door, Message, Tenant

from .conftest import TENANT_ID


async def test_get_tenants(client):
    tenants = await client.get_tenants()
    assert len(tenants) == 1
    assert isinstance(tenants[0], Tenant)
    assert tenants[0].id == TENANT_ID
    assert tenants[0].name == "Unit 101"


async def test_get_doors(client):
    tenant = (await client.get_tenants())[0]
    doors = await tenant.get_doors()

    assert [d.id for d in doors] == ["ap-1", "ap-2"]
    assert all(isinstance(d, Door) for d in doors)
    lobby, garage = doors
    assert lobby.name == "Front Lobby"
    assert lobby.online is True
    assert lobby.open_duration == 5
    assert lobby.building_name == "Test Building"
    assert lobby.tenant_id == TENANT_ID
    assert garage.building_name == "Unknown"


async def test_get_messages(client):
    tenant = (await client.get_tenants())[0]
    msgs = await tenant.get_messages()

    assert all(isinstance(m, Message) for m in msgs)
    assert msgs[0].id == "m-2"
    assert msgs[0].body == "Delivery at the door"
    assert msgs[0].source == "Front Lobby"
    assert msgs[0].image_url == "https://img/m2.jpg"
    assert msgs[1].source == "Unknown"


async def test_get_calls(client):
    tenant = (await client.get_tenants())[0]
    calls = await tenant.get_calls()

    assert len(calls) == 1 and isinstance(calls[0], Call)
    assert calls[0].status == "OPENED_DOOR"
    assert calls[0].device == "Front Lobby"


async def test_get_access_logs(client):
    tenant = (await client.get_tenants())[0]
    logs = await tenant.get_access_logs()

    assert len(logs) == 1 and isinstance(logs[0], Access)
    assert logs[0].door_name == "Front Lobby"
    assert logs[0].device_name == "Lobby Panel"
    assert logs[0].method == "SWIPE_TO_OPEN"


async def test_unknown_tenant_returns_empty_lists(client):
    tenant = Tenant({"id": "someone-else", "name": "x"}, client=client)
    assert await tenant.get_doors() == []
    assert await tenant.get_messages() == []
    assert await tenant.get_calls() == []
    assert await tenant.get_access_logs() == []


async def test_graphql_errors_return_empty_lists(client, fake):
    tenant = (await client.get_tenants())[0]
    fake.graphql_override = (200, '{"data": null, "errors": [{"message": "schema changed"}]}')
    assert await client.get_tenants() == []
    assert await tenant.get_doors() == []
    assert await tenant.get_messages() == []
    assert await tenant.get_calls() == []
    assert await tenant.get_access_logs() == []


async def test_http_failure_returns_empty_lists(client, fake):
    tenant = (await client.get_tenants())[0]
    fake.graphql_override = (503, "unavailable")
    assert await client.get_tenants() == []
    assert await tenant.get_calls() == []
