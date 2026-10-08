import json
import os
import stat
import time

from butterflymx import ButterflyMXClient

from .conftest import PASSWORD


async def test_full_login_gets_and_saves_tokens(make_client, fake, tmp_path):
    client = make_client()
    assert await client.login()

    assert client.access_token in fake.valid_access_tokens
    assert client.refresh_token in fake.valid_refresh_tokens
    assert client.expires_at > time.time() + 86000
    assert fake.requests == ["authorize", "login", "authorize_redirect", "token:authorization_code"]

    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved["access_token"] == client.access_token
    assert saved["refresh_token"] == client.refresh_token


async def test_token_file_is_owner_only(client, tmp_path):
    mode = stat.S_IMODE(os.stat(tmp_path / "tokens.json").st_mode)
    assert mode == 0o600


async def test_wrong_password_fails(make_client, fake):
    client = make_client(password="wrong")
    assert not await client.login()
    assert client.access_token is None
    assert "token:authorization_code" not in fake.requests


async def test_missing_authenticity_token_fails(make_client, fake):
    fake.login_page_has_token = False
    assert not await make_client().login()
    assert "login" not in fake.requests


async def test_state_mismatch_aborts_login(make_client, fake):
    fake.redirect_state_override = "attacker-state"
    assert not await make_client().login()
    assert "token:authorization_code" not in fake.requests


async def test_nonce_and_state_are_random_per_login(make_client, fake):
    await make_client(token_file=None).login()
    await make_client(token_file=None).login()
    states = list(fake._pending)
    assert len(states) == 2 and states[0] != states[1]


async def test_valid_token_skips_network(client, fake):
    fake.requests.clear()
    assert await client.login()
    assert fake.requests == []


async def test_expiring_token_is_refreshed(client, fake):
    old_access, old_refresh = client.access_token, client.refresh_token
    client.expires_at = time.time() + 30  # inside the 60s buffer
    fake.requests.clear()

    assert await client.login()

    assert fake.requests == ["token:refresh_token"]
    assert client.access_token != old_access
    assert client.refresh_token != old_refresh  # rotated
    assert old_refresh not in fake.valid_refresh_tokens


async def test_refresh_keeps_refresh_token_when_not_rotated(client, fake):
    fake.rotate_refresh_tokens = False
    old_refresh = client.refresh_token
    client.expires_at = 0
    assert await client.login()
    assert client.refresh_token == old_refresh


async def test_rotated_refresh_token_is_persisted(client, tmp_path):
    client.expires_at = 0
    await client.login()
    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved["refresh_token"] == client.refresh_token


async def test_invalid_refresh_token_falls_back_to_full_login(client, fake):
    client.expires_at = 0
    client.refresh_token = "revoked"
    fake.requests.clear()

    assert await client.login()

    assert fake.requests[0] == "token:refresh_token"
    assert "token:authorization_code" in fake.requests
    assert client.refresh_token in fake.valid_refresh_tokens


async def test_tokens_are_loaded_from_file(client, make_client, fake):
    fake.requests.clear()
    second = make_client(password="not-needed")
    assert second.access_token == client.access_token
    assert await second.login()
    assert fake.requests == []


async def test_corrupt_token_file_is_ignored(make_client, tmp_path):
    (tmp_path / "tokens.json").write_text("{not json")
    client = make_client()
    assert client.access_token is None
    assert await client.login()


async def test_no_token_file_keeps_tokens_in_memory(make_client, tmp_path):
    client = make_client(token_file=None)
    assert await client.login()
    assert not (tmp_path / "tokens.json").exists()


async def test_client_id_override(make_client, fake):
    client = make_client(token_file=None, client_id="custom-id")
    assert client.CLIENT_ID == "custom-id"
    assert ButterflyMXClient.CLIENT_ID != "custom-id"  # class default untouched
    await client.login()
    assert list(fake._pending.values())[0][1] == "custom-id"


async def test_password_not_written_to_token_file(client, tmp_path):
    assert PASSWORD not in (tmp_path / "tokens.json").read_text()
