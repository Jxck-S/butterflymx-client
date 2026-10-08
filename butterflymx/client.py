import asyncio
import json
import logging
import os
import re
import secrets
import time
import urllib.parse

import aiohttp

from .tenant import Tenant
from .utils import generate_code_challenge, generate_code_verifier

_LOGGER = logging.getLogger(__name__)


class ButterflyMXClient:
    CLIENT_ID = "0e3aeeb7cec2782b9fb21352a4349a44405ed5d7674072416b6481d51abfd6b6"
    REDIRECT_URI = "com.butterflymx.oauth://oauth"
    BASE_URL = "https://accounts.butterflymx.com"
    API_URL = "https://api.butterflymx.com/denizen/v1/graphql"
    UNLOCK_URL = "https://api.unlock.prod.butterflymx.com/v1/access-point"
    USER_AGENT = "butterflymx/699 CFNetwork/3860.200.71 Darwin/25.1.0"

    def __init__(self, email, password, token_file="tokens.json", client_id=None):
        self.email = email
        if client_id:
            self.CLIENT_ID = client_id
        self.password = password
        self.token_file = token_file
        self.access_token = None
        self.refresh_token = None
        self.expires_at = 0
        self._auth_lock = asyncio.Lock()
        self.load_tokens()

    def load_tokens(self):
        if self.token_file and os.path.exists(self.token_file):
            try:
                with open(self.token_file) as f:
                    data = json.load(f)
                self.access_token = data.get('access_token')
                self.refresh_token = data.get('refresh_token')
                self.expires_at = data.get('expires_at', 0)
                _LOGGER.debug("Loaded tokens from %s", self.token_file)
            except (OSError, ValueError) as e:
                _LOGGER.warning("Failed to load tokens from %s: %s", self.token_file, e)

    def save_tokens(self):
        if not self.token_file:
            return

        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at
        }
        # Owner-only permissions: this file holds credentials
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        _LOGGER.debug("Saved tokens to %s", self.token_file)

    def get_headers(self):
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Content-Type": "application/json",
            "apollographql-client-name": "com.butterflymx.butterflymx-apollo-ios",
            "x-bmx-service": "denizen-api"
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def refresh_access_token(self):
        _LOGGER.debug("Refreshing access token")
        token_url = f"{self.BASE_URL}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.CLIENT_ID,
        }

        async with aiohttp.ClientSession(headers={"User-Agent": self.USER_AGENT}) as session:
            async with session.post(token_url, data=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    if 'refresh_token' in data:
                        self.refresh_token = data.get('refresh_token')

                    expires_in = data.get('expires_in', 86400)
                    self.expires_at = time.time() + expires_in
                    self.save_tokens()
                    _LOGGER.debug("Token refreshed")
                    return True
                else:
                    text = await resp.text()
                    _LOGGER.warning("Token refresh failed: %s - %s", resp.status, text)
                    return False

    async def login(self):
        # Check validity (buffer 60s)
        if self.access_token and self.expires_at > (time.time() + 60):
                        return True

        if self.refresh_token:
            if await self.refresh_access_token():
                return True
            else:
                _LOGGER.info("Refresh failed, falling back to full login")

        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        nonce = secrets.token_urlsafe(16)
        state = secrets.token_urlsafe(16)

        # 1. Authorize URL
        auth_params = {
            "client_id": self.CLIENT_ID,
            "redirect_uri": self.REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "state": state,
            "prompt": "login"
        }
        auth_url = f"{self.BASE_URL}/oauth/authorize"

        async with aiohttp.ClientSession(headers={"User-Agent": self.USER_AGENT}) as session:
            _LOGGER.debug("Fetching login page")
            async with session.get(auth_url, params=auth_params) as resp:
                if resp.status != 200:
                    _LOGGER.error("Failed to get login page: HTTP %s", resp.status)
                    return False
                login_page_text = await resp.text()

            # 2. Extract Authenticity Token
            match = re.search(r'name="authenticity_token" value="([^"]+)"', login_page_text)
            if not match:
                _LOGGER.error("Could not find authenticity_token in login page")
                return False

            authenticity_token = match.group(1)

            # 3. Post Login
            login_url = f"{self.BASE_URL}/login"
            payload = {
                "authenticity_token": authenticity_token,
                "login[email]": self.email,
                "login[password]": self.password,
                "commit": "Sign in with email"
            }

            _LOGGER.debug("Submitting login form")

            # We process redirects manually
            async with session.post(login_url, data=payload, allow_redirects=False) as resp:
                status_code = resp.status
                text = await resp.text()
                headers = resp.headers

                # Handle Turbo Stream redirect (200 OK with <turbo-stream location="...">)
                if status_code == 200 and '<turbo-stream' in text:
                    match = re.search(r'location="([^"]+)"', text)
                    if match:
                        location = match.group(1).replace("&amp;", "&")
                        _LOGGER.debug("Following Turbo Stream redirect")
                        # Follow this as a GET
                        async with session.get(f"{self.BASE_URL}{location}", allow_redirects=False) as sub_resp:
                            status_code = sub_resp.status
                            headers = sub_resp.headers

            # Loop for redirects
            loop_count = 0
            while status_code in [301, 302, 303, 307, 308] and loop_count < 10:
                location = headers.get('Location')
                if not location:
                    break

                if location.startswith(self.REDIRECT_URI):
                    parsed = urllib.parse.urlparse(location)
                    params = urllib.parse.parse_qs(parsed.query)
                    code = params.get('code', [None])[0]
                    if params.get('state', [None])[0] != state:
                        _LOGGER.error("State mismatch in OAuth redirect, aborting login")
                        return False

                    if code:
                        return await self._exchange_code(code, verifier)
                    else:
                        _LOGGER.error("No code found in OAuth redirect")
                        return False

                # Follow HTTP redirects
                async with session.get(location, allow_redirects=False) as resp:
                    status_code = resp.status
                    headers = resp.headers
                loop_count += 1

            _LOGGER.error("Login failed: no OAuth redirect received (check email/password)")
            return False

    async def _exchange_code(self, code, verifier):
        _LOGGER.debug("Exchanging authorization code for token")
        token_url = f"{self.BASE_URL}/oauth/token"
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.CLIENT_ID,
            "redirect_uri": self.REDIRECT_URI,
            "code_verifier": verifier
        }

        async with aiohttp.ClientSession(headers={"User-Agent": self.USER_AGENT}) as session:
            async with session.post(token_url, data=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    self.refresh_token = data.get("refresh_token")

                    expires_in = data.get('expires_in', 86400)
                    self.expires_at = time.time() + expires_in
                    self.save_tokens()

                    _LOGGER.info("Logged in to ButterflyMX")
                    return True
                else:
                    text = await resp.text()
                    _LOGGER.error("Token exchange failed: %s - %s", resp.status, text)
                    return False

    async def ensure_token(self, force=False):
        """Make sure we hold a valid access token, refreshing or re-logging in if needed."""
        async with self._auth_lock:
            if force:
                self.expires_at = 0
            return await self.login()

    async def authed_post(self, url, payload):
        """POST JSON with auth. Refreshes the token and retries once on 401.
        Returns (status, body_text)."""
        if not await self.ensure_token():
            _LOGGER.error("Not authenticated")
            return None, None

        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self.get_headers()) as resp:
                    status = resp.status
                    text = await resp.text()
            if status == 401 and attempt == 0:
                _LOGGER.info("Got 401, refreshing token and retrying")
                if not await self.ensure_token(force=True):
                    break
                continue
            break
        return status, text

    async def query_graphql(self, query, variables=None):
        payload = {"query": query, "variables": variables or {}}
        status, text = await self.authed_post(self.API_URL, payload)
        if status == 200:
            try:
                data = json.loads(text)
            except ValueError:
                _LOGGER.error("GraphQL returned invalid JSON: %s", text[:200])
                return None
            if data.get("errors"):
                _LOGGER.warning("GraphQL errors: %s", data["errors"])
            return data
        _LOGGER.error("GraphQL query failed: %s - %s", status, text)
        return None

    async def get_tenants(self):
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
        tenants = ((data or {}).get('data') or {}).get('tenants') or {}
        nodes = tenants.get('nodes')
        if nodes is None:
            _LOGGER.error("Could not fetch tenants, unexpected response: %s", data)
            return []
        return [Tenant(t, client=self) for t in nodes]
