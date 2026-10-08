import asyncio
import socket
import time

import aiohttp
import pytest

from butterflymx import (
    ButterflyMXApiError,
    ButterflyMXAuthError,
    ButterflyMXConnectionError,
    ButterflyMXError,
)

QUERY = "query Tenants { tenants { nodes { id } } }"


async def test_expired_token_refreshed_before_request(client, fake):
    client.expires_at = time.time() - 10
    fake.requests.clear()

    assert await client.get_tenants()
    assert fake.requests == ["token:refresh_token", "graphql"]


async def test_401_refreshes_and_retries_once(client, fake):
    fake.valid_access_tokens.clear()  # server revoked the token early
    fake.requests.clear()

    assert await client.get_tenants()
    assert fake.requests == ["graphql", "token:refresh_token", "graphql"]


async def test_persistent_401_raises_auth_error(client, fake):
    fake.graphql_override = (401, "unauthorized")  # rejected even after a refresh
    fake.requests.clear()

    with pytest.raises(ButterflyMXAuthError):
        await client.query_graphql(QUERY)
    assert fake.count("graphql") == 2


async def test_concurrent_requests_refresh_once(client, fake):
    client.expires_at = 0
    fake.requests.clear()

    results = await asyncio.gather(*(client.get_tenants() for _ in range(5)))

    assert all(results)
    assert fake.count("token:refresh_token") == 1


async def test_concurrent_401s_refresh_once(client, fake):
    fake.valid_access_tokens.clear()
    fake.requests.clear()

    results = await asyncio.gather(*(client.get_tenants() for _ in range(5)))

    assert all(results)
    assert fake.count("token:refresh_token") == 1


async def test_unauthenticated_client_logs_in_on_first_request(make_client, fake):
    client = make_client()
    assert await client.get_tenants()
    assert "token:authorization_code" in fake.requests


async def test_failed_login_raises_before_request(make_client, fake):
    client = make_client(password="wrong")
    with pytest.raises(ButterflyMXAuthError):
        await client.query_graphql(QUERY)
    assert "graphql" not in fake.requests


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (500, "boom", ButterflyMXConnectionError),
        (503, "unavailable", ButterflyMXConnectionError),
        (400, "bad request", ButterflyMXApiError),
        (403, "forbidden", ButterflyMXAuthError),
        (200, "<html>maintenance</html>", ButterflyMXApiError),
        (200, '{"data": null, "errors": [{"message": "bad field"}]}', ButterflyMXApiError),
    ],
)
async def test_error_responses_raise(client, fake, status, body, error):
    fake.graphql_override = (status, body)
    with pytest.raises(error):
        await client.query_graphql(QUERY)


async def test_all_errors_share_base_class(client, fake):
    fake.graphql_override = (500, "boom")
    with pytest.raises(ButterflyMXError):
        await client.query_graphql(QUERY)


async def test_partial_graphql_errors_return_data(client, fake):
    fake.graphql_override = (200, '{"data": {"x": 1}, "errors": [{"message": "one field failed"}]}')
    data = await client.query_graphql(QUERY)
    assert data["data"] == {"x": 1}


async def test_timeout_raises_connection_error(make_client, fake):
    client = make_client(request_timeout=0.2)
    await client.login()
    fake.graphql_delay = 1
    with pytest.raises(ButterflyMXConnectionError):
        await client.query_graphql(QUERY)


async def test_unreachable_server_raises_connection_error(make_client):
    with socket.socket() as s:  # grab a free port, then close it so nothing listens
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    client = make_client(token_file=None)
    client.BASE_URL = f"http://127.0.0.1:{port}"
    with pytest.raises(ButterflyMXConnectionError):
        await client.login()


async def test_user_agent_override(make_client, fake):
    client = make_client(user_agent="my-agent/1.0")
    await client.get_tenants()
    assert fake.user_agents == ["my-agent/1.0"]


async def test_headers_mimic_ios_app(client):
    headers = client._api_headers()
    assert headers["Authorization"] == f"Bearer {client.access_token}"
    assert headers["apollographql-client-name"] == "com.butterflymx.butterflymx-apollo-ios"
    assert headers["User-Agent"] == client.USER_AGENT


async def test_provided_session_is_used_and_not_closed(make_client):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        client = make_client(session=session)
        assert await client.get_tenants()
        client.expires_at = 0  # also go through a refresh
        assert await client.get_tenants()
        assert client._get_session() is session
        # The accounts site's cookies (login form, token endpoint) never reach the caller's session
        assert len(session.cookie_jar) == 0
        await client.close()
        assert not session.closed


async def test_owned_session_closed_by_context_manager(make_client):
    client = make_client()
    async with client:
        await client.get_tenants()
        session = client._get_session()
    assert session.closed
