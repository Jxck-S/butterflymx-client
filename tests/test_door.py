import pytest

from butterflymx import ButterflyMXApiError, ButterflyMXAuthError, Door

from .conftest import TENANT_ID


def make_door(client, door_id="ap-1"):
    return Door({"id": door_id, "name": "Front Lobby"}, tenant_id=TENANT_ID, client=client)


async def test_open_sends_unlock_request(client, fake):
    door = (await (await client.get_tenants())[0].get_doors())[0]

    await door.open()

    assert fake.unlocks == [{"accessPointId": "ap-1", "source": "mobile_app", "tenantId": TENANT_ID}]


async def test_open_refreshes_expired_token(client, fake):
    client.expires_at = 0
    fake.requests.clear()

    await make_door(client).open()
    assert fake.requests == ["token:refresh_token", "unlock"]


async def test_open_retries_after_401(client, fake):
    fake.valid_access_tokens.clear()
    fake.requests.clear()

    await make_door(client).open()
    assert fake.requests == ["unlock", "token:refresh_token", "unlock"]


async def test_open_with_bad_credentials_raises(make_client, fake):
    client = make_client(password="wrong")
    with pytest.raises(ButterflyMXAuthError):
        await make_door(client).open()
    assert fake.unlocks == []


async def test_update_refreshes_online_status(client, fake):
    door = (await (await client.get_tenants())[0].get_doors())[0]
    assert door.online is True

    fake.data["accessPoints"]["nodes"][0]["online"] = False
    await door.update()

    assert door.online is False
    assert door.name == "Front Lobby"


async def test_update_missing_door_raises(client):
    with pytest.raises(ButterflyMXApiError):
        await make_door(client, "gone").update()
