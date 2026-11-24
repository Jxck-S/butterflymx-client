"""Fake ButterflyMX server for tests.

Implements just enough of the accounts (OAuth/login), GraphQL and unlock
endpoints to exercise the client end to end without touching the real API.
"""
import asyncio
import base64
import copy
import hashlib
import html
import itertools
import urllib.parse
from pathlib import Path

import pytest
from aiohttp import web

from butterflymx import ButterflyMXClient

EMAIL = "user@example.com"
PASSWORD = "hunter2"
TENANT_ID = "prod-tenant-1"
_FIXTURES = Path(__file__).parent / "fixtures"
_LOGIN_FIXTURE = _FIXTURES / "login_csrf.html"
_VERIFICATION_FIXTURE = _FIXTURES / "login_verification.html"
# Not on the phone verification page. Used only when a test turns resend on,
# so the client can submit a resend control the page actually contains.
_RESEND_SNIPPET = """
<form method="POST" action="/login/resend">
  <input type="hidden" name="_csrf_token" value="__CSRF_TOKEN__">
  <button type="submit">Resend code</button>
</form>
"""

# The page a wrong password lands on (303 -> GET /login/new). Sanitized: no token
# from the real site, just the markup the client classifies.
LOGIN_ERROR_PAGE = """
<form action="/login" method="post">
  <input type="hidden" name="_csrf_token" value="csrf-123">
  <input type="email" name="email" value="">
  <input type="password" name="password">
  <ul class="select-none text-sm text-red-1">Invalid email or password</ul>
</form>
"""


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
        self.login_page_format = "rails"  # "rails" (authenticity_token) or "csrf" (_csrf_token)
        self.login_requires_verification = False
        self.verification_via_redirect = False
        # Nov 2026: after the code, authorize answers 200 "Returning to app…" (link + JS) instead of 302.
        self.app_redirect_page = False
        self.verification_offers_resend = False
        self.resend_status = None  # force this HTTP status from the resend endpoint
        self.verification_code = "123456"
        self._verify_csrf = "csrf-verify"
        self._verify_return_to = None
        self._verify_state = None
        self.last_login_form = None
        self.last_verify_form = None
        self.verify_cookie = None
        self.graphql_override = None  # (status, body) to return instead of real data
        self.graphql_delay = 0.0  # seconds to stall GraphQL responses (timeout tests)
        self.authorize_delay = 0.0
        self.verify_delay = 0.0
        self.login_error_delay = 0.0
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
        if self.authorize_delay:
            await asyncio.sleep(self.authorize_delay)
        q = request.query
        if q["state"] in self._authenticated:
            self.requests.append("authorize_redirect")
            state = self.redirect_state_override or q["state"]
            code = f"code-{next(self.counter)}"
            self._codes[code] = q["code_challenge"]
            if self.app_redirect_page:
                page = (Path(__file__).parent / "fixtures" / "oauth_returning_to_app.html").read_text()
                page = page.replace("{code}", code).replace("{state}", state)
                return web.Response(text=page, content_type="text/html")
            raise web.HTTPFound(f"com.butterflymx.oauth://oauth?code={code}&state={state}")
        self.requests.append("authorize")
        self._pending[q["state"]] = (q["code_challenge"], q["client_id"])
        if not self.login_page_has_token:
            body = "<form></form>"
        elif self.login_page_format == "csrf":
            body = self._csrf_page(q)
        else:
            body = '<form><input name="authenticity_token" value="csrf-123"></form>'
        resp = web.Response(text=body, content_type="text/html")
        resp.set_cookie("_accounts_session", "abc")  # Rails session cookie
        return resp

    def _csrf_page(self, query):
        """The current login page, with this authorize request's return_to filled in."""
        return_to = "/oauth/authorize?" + urllib.parse.urlencode(
            {
                "client_id": query["client_id"],
                "redirect_uri": query["redirect_uri"],
                "response_type": query.get("response_type", "code"),
                "state": query["state"],
                "code_challenge": query["code_challenge"],
                "code_challenge_method": query.get("code_challenge_method", "S256"),
            }
        )
        template = _LOGIN_FIXTURE.read_text()
        return template.replace("__CSRF_TOKEN__", "csrf-123").replace(
            "__RETURN_TO__", html.escape(return_to, quote=True)
        )

    async def login_error_page(self, request):
        if self.login_error_delay:
            await asyncio.sleep(self.login_error_delay)
        self.requests.append("login_error")
        return web.Response(text=LOGIN_ERROR_PAGE, content_type="text/html")

    async def login(self, request):
        self.requests.append("login")
        form = await request.post()
        self.last_login_form = {key: form.get(key) for key in form}
        if self.login_page_format == "csrf":
            return self._csrf_login_result(form)
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

    def _csrf_login_result(self, form):
        """303 back to return_to on success, 303 to /login/new when the password is wrong.

        Mirrors the identity service: the posted return_to is the authorize URL
        from the login form, and a bad password redirects to the login page.
        """
        return_to = form.get("return_to") or ""
        state = urllib.parse.parse_qs(urllib.parse.urlparse(return_to).query).get("state", [None])[0]
        ok = (
            form.get("_csrf_token") == "csrf-123"
            and form.get("email") == EMAIL
            and form.get("password") == PASSWORD
            and state in self._pending
        )
        if not ok:
            raise web.HTTPSeeOther("/login/new")
        if self.login_requires_verification:
            self._verify_state = state
            self._verify_return_to = return_to
            self._verify_csrf = "csrf-verify"
            if self.verification_via_redirect:
                response = web.Response(status=303, headers={"Location": "/login/verify"})
                response.set_cookie("identity_session", "verify-session")
                return response
            return self._verification_response(invalid=False)
        self._authenticated.add(state)
        raise web.HTTPSeeOther(return_to)

    def _verification_response(self, *, invalid):
        page = _VERIFICATION_FIXTURE.read_text().replace("__CSRF_TOKEN__", self._verify_csrf)
        error = '<div class="select-none text-sm text-red-1">Invalid verification code</div>' if invalid else ""
        page = page.replace("<!--ERROR-->", error)
        if self.verification_offers_resend:
            page += _RESEND_SNIPPET.replace("__CSRF_TOKEN__", self._verify_csrf)
        response = web.Response(text=page, content_type="text/html")
        response.set_cookie("identity_session", "verify-session")
        return response

    async def verify_page(self, request):
        self.requests.append("verify_page")
        return self._verification_response(invalid=False)

    async def verify(self, request):
        if self.verify_delay:
            await asyncio.sleep(self.verify_delay)
        self.requests.append("verify")
        form = await request.post()
        self.last_verify_form = {key: form.get(key) for key in form}
        self.verify_cookie = request.cookies.get("identity_session")
        if self.verify_cookie != "verify-session":
            return web.Response(status=401, text="missing login session")
        if form.get("_csrf_token") != self._verify_csrf:
            return web.json_response({"error": "CSRF_ERROR", "error_description": "CSRF token mismatch"}, status=403)
        if form.get("code") != self.verification_code:
            self._verify_csrf = "csrf-verify-2"
            return self._verification_response(invalid=True)
        self._authenticated.add(self._verify_state)
        raise web.HTTPSeeOther(self._verify_return_to)

    async def resend_code(self, request):
        self.requests.append("resend")
        if self.resend_status:
            return web.Response(status=self.resend_status, text="session expired")
        form = await request.post()
        if form.get("_csrf_token") != self._verify_csrf:
            return web.json_response({"error": "CSRF_ERROR"}, status=403)
        self._verify_csrf = "csrf-verify-resent"
        return self._verification_response(invalid=False)

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
        app.router.add_get("/login/new", self.login_error_page)
        app.router.add_get("/login/verify", self.verify_page)
        app.router.add_post("/login/phone/verify", self.verify)
        app.router.add_post("/login/resend", self.resend_code)
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
