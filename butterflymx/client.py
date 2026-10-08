from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .exceptions import (
    ButterflyMXApiError,
    ButterflyMXAuthError,
    ButterflyMXConnectionError,
)
from .tenant import Tenant
from .utils import generate_code_challenge, generate_code_verifier

_LOGGER = logging.getLogger(__name__)

TokenCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

# Refresh this many seconds before the access token actually expires
EXPIRY_BUFFER = 60
DEFAULT_EXPIRES_IN = 86400


class ButterflyMXClient:
    CLIENT_ID = "0e3aeeb7cec2782b9fb21352a4349a44405ed5d7674072416b6481d51abfd6b6"
    REDIRECT_URI = "com.butterflymx.oauth://oauth"
    BASE_URL = "https://accounts.butterflymx.com"
    API_URL = "https://api.butterflymx.com/denizen/v1/graphql"
    UNLOCK_URL = "https://api.unlock.prod.butterflymx.com/v1/access-point"
    USER_AGENT = "butterflymx/699 CFNetwork/3860.200.71 Darwin/25.1.0"

    def __init__(
        self,
        email: str,
        password: str,
        *,
        session: aiohttp.ClientSession | None = None,
        token_file: str | os.PathLike[str] | None = None,
        tokens: dict[str, Any] | None = None,
        on_tokens_updated: TokenCallback | None = None,
        client_id: str | None = None,
        user_agent: str | None = None,
        request_timeout: float = 15.0,
    ) -> None:
        """Create a client.

        Args:
            email: ButterflyMX account email.
            password: ButterflyMX account password. Only used when a full login is needed.
            session: aiohttp session to use for API requests. If omitted, the client
                creates its own; call `close()` (or use `async with`) to clean it up.
            token_file: Optional JSON file to load tokens from and save them to.
            tokens: Previously saved tokens (as returned by `tokens`) to start with.
            on_tokens_updated: Called with the new tokens dict whenever they change,
                so you can persist them yourself. May be sync or async.
            client_id: Override the OAuth client ID.
            user_agent: Override the User-Agent header.
            request_timeout: Total timeout in seconds for each HTTP request.
        """
        self.email = email
        self.password = password
        if client_id:
            self.CLIENT_ID = client_id
        if user_agent:
            self.USER_AGENT = user_agent
        self.token_file = token_file
        self.on_tokens_updated = on_tokens_updated
        self.timeout = aiohttp.ClientTimeout(total=request_timeout)

        self._session = session
        self._owns_session = session is None
        self._auth_lock = asyncio.Lock()
        # Load the token file lazily (off the event loop) unless tokens were given
        self._tokens_loaded = tokens is not None or not token_file

        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0
        if tokens:
            self._set_tokens(tokens)

    # --- lifecycle ---

    async def __aenter__(self) -> ButterflyMXClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session if this client created it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    # --- token storage ---

    @property
    def tokens(self) -> dict[str, Any]:
        """Current tokens, in the format accepted by `tokens=`."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    def _set_tokens(self, data: dict[str, Any]) -> None:
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.expires_at = data.get("expires_at") or 0

    def _read_token_file(self) -> dict[str, Any] | None:
        assert self.token_file
        try:
            with open(self.token_file) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            _LOGGER.warning("Failed to load tokens from %s: %s", self.token_file, e)
            return None

    def _write_token_file(self, data: dict[str, Any]) -> None:
        assert self.token_file
        # Owner-only permissions: this file holds credentials
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)

    async def _load_tokens(self) -> None:
        if self._tokens_loaded:
            return
        self._tokens_loaded = True
        data = await asyncio.to_thread(self._read_token_file)
        if data:
            self._set_tokens(data)
            _LOGGER.debug("Loaded tokens from %s", self.token_file)

    async def _save_tokens(self) -> None:
        data = self.tokens
        if self.token_file:
            await asyncio.to_thread(self._write_token_file, data)
            _LOGGER.debug("Saved tokens to %s", self.token_file)
        if self.on_tokens_updated:
            result = self.on_tokens_updated(dict(data))
            if inspect.isawaitable(result):
                await result

    async def _store_token_response(self, data: dict[str, Any]) -> None:
        self.access_token = data.get("access_token")
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        self.expires_at = time.time() + data.get("expires_in", DEFAULT_EXPIRES_IN)
        await self._save_tokens()

    def _token_is_valid(self) -> bool:
        return bool(self.access_token) and self.expires_at > time.time() + EXPIRY_BUFFER

    # --- HTTP helpers ---

    async def _request(
        self, method: str, url: str, *, session: aiohttp.ClientSession | None = None, **kwargs: Any
    ) -> tuple[int, str, Any]:
        """Send a request; return (status, body, headers). Network errors become ButterflyMXConnectionError."""
        session = session or self._get_session()
        kwargs.setdefault("timeout", self.timeout)
        try:
            async with session.request(method, url, **kwargs) as resp:
                return resp.status, await resp.text(), resp.headers
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ButterflyMXConnectionError(f"{method} {url} failed: {e!r}") from e

    @staticmethod
    def _raise_for_status(status: int, text: str, what: str) -> None:
        if status < 400:
            return
        msg = f"{what} failed: HTTP {status} - {text[:200]}"
        if status >= 500:
            raise ButterflyMXConnectionError(msg)
        if status in (401, 403):
            raise ButterflyMXAuthError(msg)
        raise ButterflyMXApiError(msg)

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Content-Type": "application/json",
            "apollographql-client-name": "com.butterflymx.butterflymx-apollo-ios",
            "x-bmx-service": "denizen-api",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    # --- authentication ---

    async def login(self) -> None:
        """Make sure the client is authenticated.

        Uses the current access token if still valid, otherwise refreshes it,
        otherwise does a full login. You don't need to call this yourself:
        every request does it first.

        Raises:
            ButterflyMXAuthError: wrong email/password.
            ButterflyMXConnectionError: couldn't reach ButterflyMX.
        """
        await self.ensure_token()

    async def ensure_token(self, *, invalid_token: str | None = None) -> None:
        """Make sure we hold a valid access token.

        Args:
            invalid_token: An access token the server just rejected. If it's still
                the current one, it's discarded and refreshed. If another caller
                already replaced it, nothing more happens.
        """
        async with self._auth_lock:
            await self._load_tokens()
            if invalid_token is not None and invalid_token == self.access_token:
                self.expires_at = 0
            if self._token_is_valid():
                return
            if self.refresh_token and await self._refresh_access_token():
                return
            await self._full_login()

    async def _refresh_access_token(self) -> bool:
        """Try the refresh token. Returns False if the server rejected it."""
        _LOGGER.debug("Refreshing access token")
        status, text, _ = await self._request(
            "POST",
            f"{self.BASE_URL}/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token, "client_id": self.CLIENT_ID},
            headers={"User-Agent": self.USER_AGENT},
        )
        if status >= 500:
            self._raise_for_status(status, text, "Token refresh")
        if status != 200:
            _LOGGER.info("Refresh token rejected (HTTP %s), falling back to full login", status)
            return False
        await self._store_token_response(json.loads(text))
        _LOGGER.debug("Token refreshed")
        return True

    async def _full_login(self) -> None:
        verifier = generate_code_verifier()
        state = secrets.token_urlsafe(16)
        auth_params = {
            "client_id": self.CLIENT_ID,
            "redirect_uri": self.REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile",
            "code_challenge": generate_code_challenge(verifier),
            "code_challenge_method": "S256",
            "nonce": secrets.token_urlsafe(16),
            "state": state,
            "prompt": "login",
        }

        # The login form relies on cookies, so use a throwaway session with its own
        # cookie jar instead of polluting a caller-provided session.
        async with aiohttp.ClientSession(headers={"User-Agent": self.USER_AGENT}) as session:
            _LOGGER.debug("Fetching login page")
            status, page, _ = await self._request(
                "GET", f"{self.BASE_URL}/oauth/authorize", session=session, params=auth_params
            )
            self._raise_for_status(status, page, "Fetching login page")

            match = re.search(r'name="authenticity_token" value="([^"]+)"', page)
            if not match:
                raise ButterflyMXApiError("Could not find authenticity_token on the login page")

            _LOGGER.debug("Submitting login form")
            status, text, headers = await self._request(
                "POST",
                f"{self.BASE_URL}/login",
                session=session,
                allow_redirects=False,
                data={
                    "authenticity_token": match.group(1),
                    "login[email]": self.email,
                    "login[password]": self.password,
                    "commit": "Sign in with email",
                },
            )
            self._raise_for_status(status, text, "Login")

            # Rails/Turbo answers a successful login with 200 + <turbo-stream location="...">
            if status == 200 and "<turbo-stream" in text:
                match = re.search(r'location="([^"]+)"', text)
                if match:
                    location = match.group(1).replace("&amp;", "&")
                    _LOGGER.debug("Following Turbo Stream redirect")
                    status, _, headers = await self._request(
                        "GET", f"{self.BASE_URL}{location}", session=session, allow_redirects=False
                    )

            for _ in range(10):
                location = headers.get("Location")
                if status not in (301, 302, 303, 307, 308) or not location:
                    break
                if location.startswith(self.REDIRECT_URI):
                    params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
                    if params.get("state", [None])[0] != state:
                        raise ButterflyMXAuthError("State mismatch in OAuth redirect")
                    code = params.get("code", [None])[0]
                    if not code:
                        raise ButterflyMXAuthError("No authorization code in OAuth redirect")
                    await self._exchange_code(code, verifier)
                    return
                status, _, headers = await self._request("GET", location, session=session, allow_redirects=False)

        raise ButterflyMXAuthError("Login failed: no OAuth redirect received (check email/password)")

    async def _exchange_code(self, code: str, verifier: str) -> None:
        _LOGGER.debug("Exchanging authorization code for token")
        status, text, _ = await self._request(
            "POST",
            f"{self.BASE_URL}/oauth/token",
            headers={"User-Agent": self.USER_AGENT},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.CLIENT_ID,
                "redirect_uri": self.REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        if 400 <= status < 500:
            raise ButterflyMXAuthError(f"Token exchange failed: HTTP {status} - {text[:200]}")
        self._raise_for_status(status, text, "Token exchange")
        await self._store_token_response(json.loads(text))
        _LOGGER.info("Logged in to ButterflyMX")

    # --- API ---

    async def _authed_post(self, url: str, payload: dict[str, Any]) -> tuple[int, str]:
        """POST JSON with auth. Refreshes the token and retries once on 401."""
        await self.ensure_token()
        for attempt in range(2):
            token = self.access_token
            status, text, _ = await self._request("POST", url, json=payload, headers=self._api_headers())
            if status != 401 or attempt == 1:
                break
            _LOGGER.info("Got 401, refreshing token and retrying")
            await self.ensure_token(invalid_token=token)
        if status == 401:
            raise ButterflyMXAuthError("Request rejected with 401 even after refreshing the token")
        self._raise_for_status(status, text, f"POST {url}")
        return status, text

    async def query_graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a GraphQL query and return the full response JSON.

        Raises:
            ButterflyMXApiError: the response had errors and no data, or wasn't JSON.
        """
        _, text = await self._authed_post(self.API_URL, {"query": query, "variables": variables or {}})
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ButterflyMXApiError(f"GraphQL returned invalid JSON: {text[:200]}") from e
        if data.get("errors"):
            if not data.get("data"):
                raise ButterflyMXApiError(f"GraphQL errors: {data['errors']}")
            _LOGGER.warning("GraphQL returned partial errors: %s", data["errors"])
        return data

    async def get_tenants(self) -> list[Tenant]:
        """The tenants (units) on this account."""
        query = """
        query Tenants {
            tenants {
                nodes {
                    id
                    name
                }
            }
        }
        """
        data = await self.query_graphql(query)
        nodes = ((data.get("data") or {}).get("tenants") or {}).get("nodes")
        if nodes is None:
            raise ButterflyMXApiError(f"Unexpected tenants response: {data}")
        return [Tenant(t, client=self) for t in nodes]
