from butterflymx import Door

from .conftest import TENANT_ID


async def test_open_sends_unlock_request(client, fake):
    door = (await (await client.get_tenants())[0].get_doors())[0]

    assert await door.open()

    assert fake.unlocks == [{"accessPointId": "ap-1", "source": "mobile_app", "tenantId": TENANT_ID}]


async def test_open_refreshes_expired_token(client, fake):
    door = Door({"id": "ap-1", "name": "Front Lobby"}, tenant_id=TENANT_ID, client=client)
    client.expires_at = 0
    fake.requests.clear()

    assert await door.open()
    assert fake.requests == ["token:refresh_token", "unlock"]


async def test_open_retries_after_401(client, fake):
    door = Door({"id": "ap-1", "name": "Front Lobby"}, tenant_id=TENANT_ID, client=client)
    fake.valid_access_tokens.clear()
    fake.requests.clear()

    assert await door.open()
    assert fake.requests == ["unlock", "token:refresh_token", "unlock"]


async def test_open_failure_returns_false(make_client, fake):
    client = make_client(password="wrong")
    door = Door({"id": "ap-1", "name": "Front Lobby"}, tenant_id=TENANT_ID, client=client)
    assert not await door.open()
    assert fake.unlocks == []
