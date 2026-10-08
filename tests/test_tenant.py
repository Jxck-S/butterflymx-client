import pytest

from butterflymx import (
    Access,
    ButterflyMXApiError,
    ButterflyMXConnectionError,
    Call,
    Door,
    Message,
    Tenant,
    TenantOverview,
)

from .conftest import TENANT_ID


@pytest.fixture
async def tenant(client):
    return (await client.get_tenants())[0]


async def test_get_tenants(client):
    tenants = await client.get_tenants()
    assert len(tenants) == 1
    assert isinstance(tenants[0], Tenant)
    assert tenants[0].id == TENANT_ID
    assert tenants[0].name == "Unit 101"


async def test_get_doors(tenant):
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


async def test_get_door(tenant):
    door = await tenant.get_door("ap-2")
    assert door.name == "Garage"
    assert await tenant.get_door("nope") is None


async def test_get_messages(tenant):
    msgs = await tenant.get_messages()

    assert all(isinstance(m, Message) for m in msgs)
    assert msgs[0].id == "m-2"
    assert msgs[0].body == "Delivery at the door"
    assert msgs[0].source == "Front Lobby"
    assert msgs[0].image_url == "https://img/m2.jpg"
    assert msgs[1].source == "Unknown"


async def test_get_calls(tenant):
    calls = await tenant.get_calls()

    assert len(calls) == 1 and isinstance(calls[0], Call)
    assert calls[0].status == "OPENED_DOOR"
    assert calls[0].device == "Front Lobby"


async def test_get_access_logs(tenant):
    logs = await tenant.get_access_logs()

    assert len(logs) == 1 and isinstance(logs[0], Access)
    assert logs[0].door_name == "Front Lobby"
    assert logs[0].device_name == "Lobby Panel"
    assert logs[0].method == "SWIPE_TO_OPEN"


async def test_get_overview_is_one_request(tenant, fake):
    fake.requests.clear()

    overview = await tenant.get_overview()

    assert fake.requests == ["graphql"]
    assert isinstance(overview, TenantOverview)
    assert [d.id for d in overview.doors] == ["ap-1", "ap-2"]
    assert [m.id for m in overview.messages] == ["m-2", "m-1"]
    assert [c.id for c in overview.calls] == ["c-1"]
    assert [a.id for a in overview.access_logs] == ["a-1"]


async def test_unknown_tenant_returns_empty_lists(client):
    tenant = Tenant({"id": "someone-else", "name": "x"}, client=client)
    assert await tenant.get_doors() == []
    assert await tenant.get_messages() == []
    assert await tenant.get_calls() == []
    assert await tenant.get_access_logs() == []
    assert await tenant.get_overview() == TenantOverview()


async def test_graphql_errors_raise(client, tenant, fake):
    fake.graphql_override = (200, '{"data": null, "errors": [{"message": "schema changed"}]}')
    for call in (client.get_tenants, tenant.get_doors, tenant.get_messages, tenant.get_calls,
                 tenant.get_access_logs, tenant.get_overview):
        with pytest.raises(ButterflyMXApiError):
            await call()


async def test_unexpected_tenants_shape_raises(client, fake):
    fake.graphql_override = (200, '{"data": {"tenants": null}}')
    with pytest.raises(ButterflyMXApiError):
        await client.get_tenants()


async def test_http_failure_raises(client, tenant, fake):
    fake.graphql_override = (503, "unavailable")
    with pytest.raises(ButterflyMXConnectionError):
        await client.get_tenants()
    with pytest.raises(ButterflyMXConnectionError):
        await tenant.get_calls()
