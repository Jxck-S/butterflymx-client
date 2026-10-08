"""Fake ButterflyMX server for tests.

Implements just enough of the accounts (OAuth/login), GraphQL and unlock
endpoints to exercise the client end to end without touching the real API.
"""
import asyncio
import base64
import copy
import hashlib
import itertools
import urllib.parse

import pytest
from aiohttp import web

from butterflymx import ButterflyMXClient

EMAIL = "user@example.com"
PASSWORD = "hunter2"
TENANT_ID = "prod-tenant-1"

TENANT_DATA = {
    "accessPoints": {"nodes": [
        {"id": "ap-1", "name": "Front Lobby", "capabilities": [], "online": True,
         "openDuration": 5, "building": {"id": "b-1", "name": "Test Building"}},
        {"id": "ap-2", "name": "Garage", "capabilities": [], "online": False,
         "openDuration": 10, "building": None},
    ]},
    "messages": {"nodes": [
        {"id": "m-2", "body": "Delivery at the door", "createdAt": "2026-01-02T00:00:00Z",
         "imageUrl": "https://img/m2.jpg", "origin": "VISITOR", "source": {"name": "Front Lobby"}},
        {"id": "m-1", "body": "Hi", "createdAt": "2026-01-01T00:00:00Z",
         "imageUrl": None, "origin": "VISITOR", "source": None},
    ]},
    "calls": {"nodes": [
        {"id": "c-1", "loggedAt": "2026-01-03T00:00:00Z", "displayStatus": "OPENED_DOOR",
         "notificationType": "VISITOR", "imageUrl": "https://img/c1.jpg", "device": {"name": "Front Lobby"}},
    ]},
    "doorReleases": {"nodes": [
        {"id": "a-1", "loggedAt": "2026-01-04T00:00:00Z", "imageUrl": "https://img/a1.jpg",
         "type": "TENANT", "method": "SWIPE_TO_OPEN", "accessPoint": {"name": "Front Lobby"},
         "device": {"name": "Lobby Panel"}},
    ]},
}


def _s256(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class FakeButterflyMX:
    """In-memory fake of the ButterflyMX servers. Tests tweak attributes to simulate failures."""

    def __init__(self):
        self.counter = itertools.count(1)
        self.valid_access_tokens = set()
        self.valid_refresh_tokens = set()
        self.expires_in = 86400
        self.rotate_refresh_tokens = True
        # Knobs for failure scenarios
        self.redirect_state_override = None
        self.login_page_has_token = True
        self.graphql_override = None  # (status, body) to return instead of real data
        self.graphql_delay = 0.0  # seconds to stall GraphQL responses (timeout tests)
        self.data = copy.deepcopy(TENANT_DATA)
        # Recorded traffic
        self.requests = []
        self.unlocks = []
        self.user_agents = []
        self._pending = {}  # state -> (challenge, client_id)
        self._authenticated = set()  # states whose login form was submitted successfully
        self._codes = {}  # code -> challenge

    def issue_tokens(self):
        n = next(self.counter)
        access, refresh = f"access-{n}", f"refresh-{n}"
        self.valid_access_tokens.add(access)
        self.valid_refresh_tokens.add(refresh)
        return access, refresh

    def count(self, name):
        return sum(1 for r in self.requests if r == name)

    # --- accounts.butterflymx.com ---

    async def authorize(self, request):
        q = request.query
        if q["state"] in self._authenticated:
            self.requests.append("authorize_redirect")
            state = self.redirect_state_override or q["state"]
            code = f"code-{next(self.counter)}"
            self._codes[code] = q["code_challenge"]
            raise web.HTTPFound(f"com.butterflymx.oauth://oauth?code={code}&state={state}")
        self.requests.append("authorize")
        self._pending[q["state"]] = (q["code_challenge"], q["client_id"])
        field = '<input name="authenticity_token" value="csrf-123">' if self.login_page_has_token else ""
        resp = web.Response(text=f"<form>{field}</form>", content_type="text/html")
        resp.set_cookie("_accounts_session", "abc")  # Rails session cookie
        return resp

    async def login(self, request):
        self.requests.append("login")
        form = await request.post()
        if (form.get("authenticity_token") != "csrf-123" or form.get("login[email]") != EMAIL
                or form.get("login[password]") != PASSWORD):
            return web.Response(text="<p>Invalid email or password</p>", content_type="text/html")
        # Mimic Rails/Turbo: 200 with a turbo-stream redirect back to authorize
        state = next(reversed(self._pending))
        challenge, client_id = self._pending[state]
        query = urllib.parse.urlencode({"client_id": client_id, "state": state, "code_challenge": challenge})
        location = "/oauth/authorize?" + query.replace("&", "&amp;")
        self._authenticated.add(state)
        return web.Response(text=f'<turbo-stream action="redirect" location="{location}"></turbo-stream>',
                            content_type="text/vnd.turbo-stream.html")

    async def token(self, request):
        form = await request.post()
        grant = form.get("grant_type")
        self.requests.append(f"token:{grant}")
        if grant == "authorization_code":
            challenge = self._codes.pop(form.get("code"), None)
            if challenge is None or _s256(form.get("code_verifier", "")) != challenge:
                return web.json_response({"error": "invalid_grant"}, status=400)
        elif grant == "refresh_token":
            rt = form.get("refresh_token")
            if rt not in self.valid_refresh_tokens:
                return web.json_response({"error": "invalid_grant"}, status=400)
            if self.rotate_refresh_tokens:
                self.valid_refresh_tokens.discard(rt)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)
        access, refresh = self.issue_tokens()
        body = {"access_token": access, "token_type": "Bearer", "expires_in": self.expires_in}
        if grant == "authorization_code" or self.rotate_refresh_tokens:
            body["refresh_token"] = refresh
        resp = web.json_response(body)
        resp.set_cookie("_accounts_session", "abc")  # the real token endpoint sets this too
        return resp

    # --- api.butterflymx.com ---

    def _authorized(self, request):
        auth = request.headers.get("Authorization", "")
        return auth.removeprefix("Bearer ") in self.valid_access_tokens

    async def graphql(self, request):
        self.requests.append("graphql")
        self.user_agents.append(request.headers.get("User-Agent"))
        if self.graphql_delay:
            await asyncio.sleep(self.graphql_delay)
        if not self._authorized(request):
            return web.Response(status=401, text='{"error":"unauthorized"}')
        if self.graphql_override:
            status, body = self.graphql_override
            return web.Response(status=status, text=body)
        payload = await request.json()
        query = payload["query"]
        if "query Tenants" in query:
            return web.json_response({"data": {"tenants": {"nodes": [{"id": TENANT_ID, "name": "Unit 101"}]}}})
        # All other queries are nodes(ids: [tenant]) { ... on Tenant { <field> } }
        ids = next(iter(payload["variables"].values()))
        if ids != [TENANT_ID]:
            return web.json_response({"data": {"nodes": [None]}})
        node = {"id": TENANT_ID}
        node.update({k: v for k, v in self.data.items() if k in query})
        if len(node) == 1:
            return web.json_response({"errors": [{"message": "unknown query"}]})
        return web.json_response({"data": {"nodes": [node]}})

    async def unlock(self, request):
        self.requests.append("unlock")
        if not self._authorized(request):
            return web.Response(status=401)
        self.unlocks.append(await request.json())
        return web.Response(status=204)

    def app(self):
        app = web.Application()
        app.router.add_get("/oauth/authorize", self.authorize)
        app.router.add_post("/login", self.login)
        app.router.add_post("/oauth/token", self.token)
        app.router.add_post("/graphql", self.graphql)
        app.router.add_post("/unlock", self.unlock)
        return app


@pytest.fixture
def fake():
    return FakeButterflyMX()


@pytest.fixture
async def server(aiohttp_server, fake):
    return await aiohttp_server(fake.app())


@pytest.fixture
async def make_client(server, tmp_path):
    """Build a client pointed at the fake server. Tokens go to a temp file."""
    base = str(server.make_url("")).rstrip("/")
    clients = []

    def _make(email=EMAIL, password=PASSWORD, token_file=tmp_path / "tokens.json", **kwargs):
        client = ButterflyMXClient(email, password, token_file=token_file, **kwargs)
        client.BASE_URL = base
        client.API_URL = f"{base}/graphql"
        client.UNLOCK_URL = f"{base}/unlock"
        clients.append(client)
        return client

    yield _make
    for c in clients:
        await c.close()


@pytest.fixture
async def client(make_client):
    """A client that has already logged in."""
    c = make_client()
    await c.login()
    return c
