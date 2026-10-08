import asyncio
import time


async def test_expired_token_refreshed_before_request(client, fake):
    client.expires_at = time.time() - 10
    fake.requests.clear()

    tenants = await client.get_tenants()

    assert tenants
    assert fake.requests == ["token:refresh_token", "graphql"]


async def test_401_refreshes_and_retries_once(client, fake):
    fake.valid_access_tokens.clear()  # server revoked the token early
    fake.requests.clear()

    tenants = await client.get_tenants()

    assert tenants
    assert fake.requests == ["graphql", "token:refresh_token", "graphql"]


async def test_persistent_401_does_not_loop(client, fake):
    fake.graphql_override = (401, "unauthorized")  # rejected even after a refresh
    fake.requests.clear()

    assert await client.query_graphql("query Tenants { tenants { nodes { id } } }") is None
    assert fake.count("graphql") == 2


async def test_concurrent_requests_refresh_once(client, fake):
    client.expires_at = 0
    fake.requests.clear()

    results = await asyncio.gather(*(client.get_tenants() for _ in range(5)))

    assert all(results)
    assert fake.count("token:refresh_token") == 1


async def test_unauthenticated_client_logs_in_on_first_request(make_client, fake):
    client = make_client()
    tenants = await client.get_tenants()
    assert tenants
    assert "token:authorization_code" in fake.requests


async def test_failed_login_returns_none(make_client, fake):
    client = make_client(password="wrong")
    assert await client.query_graphql("query Tenants { x }") is None
    assert "graphql" not in fake.requests


async def test_http_error_returns_none(client, fake):
    fake.graphql_override = (500, "boom")
    assert await client.query_graphql("query Tenants { x }") is None


async def test_invalid_json_returns_none(client, fake):
    fake.graphql_override = (200, "<html>maintenance</html>")
    assert await client.query_graphql("query Tenants { x }") is None


async def test_graphql_errors_are_returned(client, fake):
    fake.graphql_override = (200, '{"data": null, "errors": [{"message": "bad field"}]}')
    data = await client.query_graphql("query Tenants { x }")
    assert data["errors"][0]["message"] == "bad field"


async def test_headers_mimic_ios_app(client):
    headers = client.get_headers()
    assert headers["Authorization"] == f"Bearer {client.access_token}"
    assert headers["apollographql-client-name"] == "com.butterflymx.butterflymx-apollo-ios"
    assert headers["User-Agent"] == client.USER_AGENT
