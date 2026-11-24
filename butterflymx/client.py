from __future__ import annotations

import asyncio
import html
import inspect
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import aiohttp

from .exceptions import (
    ButterflyMXApiError,
    ButterflyMXAuthError,
    ButterflyMXConnectionError,
    ButterflyMXInvalidVerificationCode,
    ButterflyMXVerificationRequired,
)
from .tenant import Tenant
from .utils import generate_code_challenge, generate_code_verifier

_LOGGER = logging.getLogger(__name__)

TokenCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

# Refresh this many seconds before the access token actually expires
EXPIRY_BUFFER = 60
DEFAULT_EXPIRES_IN = 86400
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# The accounts site has used both names: Rails `authenticity_token`, then the
# identity service's `_csrf_token`. Field names changed with them.
_TOKEN_FIELDS = ("authenticity_token", "_csrf_token")
_EMAIL_FIELDS = ("login[email]", "email")
_PASSWORD_FIELDS = ("login[password]", "password")
# The phone sign-in code page (Nov 2026) uses name="code" and
# autocomplete="one-time-code". The other names are the same idea on a form
# we have not been shown yet (email two-factor needs a real password).
_CODE_FIELD_NAMES = {"code", "otp", "verification_code", "one_time_code", "otp_attempt"}
_INVALID_CODE_TEXT = (
    "invalid verification code",
    "verification code expired",
    "expired verification code",
    "code has expired",
)
_RESEND_RE = re.compile(r"\bresend\b")


class _LoginFormParser(HTMLParser):
    """Collect each form's action and inputs, plus links, from a login page."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self.anchors: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None
        self._button: dict[str, Any] | None = None
        self._anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "form":
            self._current = {
                "action": attr.get("action") or "",
                "method": (attr.get("method") or "GET").upper(),
                "inputs": [],
            }
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append(attr)
        elif tag == "button" and self._current is not None:
            self._button = {
                "type": attr.get("type") or "submit",
                "name": attr.get("name"),
                "value": attr.get("value"),
                "text": "",
            }
            self._current["inputs"].append(self._button)
        elif tag == "a":
            self._anchor = {"href": attr.get("href") or "", "text": ""}

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button["text"] += data
        if self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None
            self._button = None
        elif tag == "button":
            self._button = None
        elif tag == "a" and self._anchor is not None:
            self.anchors.append(self._anchor)
            self._anchor = None


def _select_login_form(forms: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The email/password form, not the Microsoft/Google SSO forms beside it."""

    def names(form: dict[str, Any]) -> set[str]:
        return {item.get("name") for item in form["inputs"] if item.get("name")}

    for form in forms:
        found = names(form)
        if found & set(_EMAIL_FIELDS) or found & set(_PASSWORD_FIELDS):
            return form
    for form in forms:
        if names(form) & set(_TOKEN_FIELDS):
            return form
    return None


def _parse_login_form(page: str, email: str, password: str) -> tuple[str, dict[str, str]] | None:
    """Return `(action, fields)` for the login POST, or None if no token is present.

    Hidden fields (including `return_to`) are copied through. HTML entities in
    values are decoded, so a `return_to` of `/oauth/authorize?a=1&b=2` is
    posted as `/oauth/authorize?a=1&b=2`.
    """
    parser = _LoginFormParser()
    parser.feed(page)
    form = _select_login_form(parser.forms)
    if form is None:
        return None

    fields: dict[str, str] = {}
    email_field: str | None = None
    password_field: str | None = None
    for item in form["inputs"]:
        name = item.get("name")
        if not name:
            continue
        value = item.get("value") or ""
        kind = (item.get("type") or "").lower()
        if name in _EMAIL_FIELDS:
            email_field = name
        elif name in _PASSWORD_FIELDS:
            password_field = name
        elif name == "commit":
            fields["commit"] = value or fields.get("commit", "")
        elif kind in ("hidden", "") and (kind == "hidden" or value or name in _TOKEN_FIELDS):
            fields[name] = value

    if not any(name in fields for name in _TOKEN_FIELDS):
        return None
    if email_field is None:
        email_field = "login[email]" if "authenticity_token" in fields else "email"
    if password_field is None:
        password_field = "login[password]" if "authenticity_token" in fields else "password"
    fields[email_field] = email
    fields[password_field] = password
    if not fields.get("commit"):
        fields["commit"] = "Sign in with email"
    return form.get("action") or "/login", fields


@dataclass
class _VerificationForm:
    """The code form currently on screen, and a resend control if the page has one."""

    action: str
    code_field: str
    fields: dict[str, str]
    resend_method: str | None = None
    resend_action: str | None = None
    resend_fields: dict[str, str] | None = None

    @property
    def can_resend(self) -> bool:
        return self.resend_action is not None


@dataclass
class _PendingVerification:
    """OAuth PKCE state plus the code form, kept with the accounts session."""

    verifier: str
    state: str
    action: str
    code_field: str
    fields: dict[str, str]
    resend_method: str | None = None
    resend_action: str | None = None
    resend_fields: dict[str, str] | None = None

    def use_form(self, form: _VerificationForm) -> None:
        self.action = form.action
        self.code_field = form.code_field
        self.fields = dict(form.fields)
        self.resend_method = form.resend_method
        self.resend_action = form.resend_action
        self.resend_fields = dict(form.resend_fields) if form.resend_fields is not None else None


def _is_code_input(item: dict[str, Any]) -> bool:
    name = (item.get("name") or "").strip()
    lowered = name.lower()
    autocomplete = (item.get("autocomplete") or "").lower()
    if autocomplete == "one-time-code":
        return True
    if lowered in _CODE_FIELD_NAMES:
        return True
    return lowered.endswith(("[code]", "[otp]", "_code", "_otp"))


def _hidden_fields(form: dict[str, Any], *, skip: set[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in form["inputs"]:
        name = item.get("name")
        if not name or name in skip:
            continue
        kind = (item.get("type") or "").lower()
        value = item.get("value") or ""
        if kind in ("hidden", "") and (kind == "hidden" or value or name in _TOKEN_FIELDS):
            fields[name] = value
    return fields


def _resend_control(form: dict[str, Any]) -> bool:
    if _RESEND_RE.search(form.get("action") or ""):
        return True
    for item in form["inputs"]:
        label = f"{item.get('value') or ''} {item.get('text') or ''}"
        if _RESEND_RE.search(label):
            return True
    return False


def _parse_verification_form(page: str, page_url: str = "") -> _VerificationForm | None:
    """The verification-code form, if this page is asking for one.

    Observed on accounts.butterflymx.com (phone sign-in, Nov 2026): a POST form
    at `/login/phone/verify` with hidden `_csrf_token` and
    `<input name="code" autocomplete="one-time-code" maxlength="6">`.
    There is no resend control, only a link back to `/login/phone`.
    Email/password two-factor was not reachable without a real account; whatever
    code form that response contains is what gets submitted.
    """
    parser = _LoginFormParser()
    parser.feed(page)
    code_form: dict[str, Any] | None = None
    code_field: str | None = None
    for form in parser.forms:
        for item in form["inputs"]:
            if item.get("name") and _is_code_input(item):
                code_form = form
                code_field = item["name"]
                break
        if code_form is not None:
            break
    if code_form is None or code_field is None:
        return None

    resend_method: str | None = None
    resend_action: str | None = None
    resend_fields: dict[str, str] | None = None
    for form in parser.forms:
        if form is code_form or not _resend_control(form):
            continue
        resend_method = form.get("method") or "POST"
        resend_action = form.get("action") or page_url
        resend_fields = _hidden_fields(form, skip=set())
        break
    if resend_action is None:
        for anchor in parser.anchors:
            if _RESEND_RE.search(anchor["text"]) or _RESEND_RE.search(anchor["href"]):
                resend_method = "GET"
                resend_action = anchor["href"]
                resend_fields = {}
                break

    return _VerificationForm(
        action=code_form.get("action") or page_url,
        code_field=code_field,
        fields=_hidden_fields(code_form, skip={code_field}),
        resend_method=resend_method,
        resend_action=resend_action,
        resend_fields=resend_fields,
    )


def _page_says_invalid_code(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in _INVALID_CODE_TEXT)


def _app_redirect_from_page(page: str, redirect_uri: str) -> str | None:
    """The `com.butterflymx.oauth://` URL on a returning-to-app page, if any.

    Observed Nov 2026 after the SMS code: HTTP 200, title "Returning to app…",
    `<a href="com.butterflymx.oauth://oauth?code=…&amp;state=…">` plus
    `window.location.replace("com.butterflymx.oauth://oauth?code=…&state=…")`.
    Only a URL that carries an authorization code counts.
    """
    parser = _LoginFormParser()
    parser.feed(page)
    candidates = [anchor["href"] for anchor in parser.anchors]
    for match in re.finditer(r"""location\.(?:replace|assign)\(\s*(["'])(.*?)\1\s*\)""", page):
        candidates.append(match.group(2).replace("\\u0026", "&").replace("\\/", "/"))
    for candidate in candidates:
        url = html.unescape(candidate.strip())
        if url.startswith(redirect_uri) and "code=" in url:
            return url
    return None


def _describe_page(status: int, text: str) -> str:
    """A short, value-free summary of an unexpected login page for the error message.

    Form actions and input names only (never input values), plus the title and
    any visible alert-like text, so a failed login can be diagnosed from logs.
    """
    parser = _LoginFormParser()
    parser.feed(text)
    forms = [
        f"{form['method']} {urllib.parse.urlparse(form['action']).path or '?'}"
        f"[{','.join(sorted({i.get('name') for i in form['inputs'] if i.get('name')}))}]"
        for form in parser.forms
    ]
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)))
    alerts = re.findall(r"[^.!?]{0,80}(?:error|invalid|incorrect|expired|wrong|try again)[^.!?]{0,80}", visible, re.I)
    return (
        f"page HTTP {status}, title={html.unescape(title.group(1).strip()) if title else None!r}, "
        f"forms={forms}, alerts={[a.strip() for a in alerts[:3]]}"
    )


def _page_says_invalid_password(text: str) -> bool:
    return "invalid email or password" in text.lower()


def _pause_cancellation(task: asyncio.Task[Any] | None) -> bool:
    """Drop pending cancellation so a cleanup await can finish.

    Returns whether this task was being cancelled. Python 3.11+ re-raises
    CancelledError at every later await until `uncancel()` runs. Python 3.10
    has neither method; a caught CancelledError is not repeated automatically.
    """
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    uncancel = getattr(task, "uncancel", None)
    if not callable(cancelling) or not callable(uncancel):
        return False
    paused = False
    while cancelling():
        uncancel()
        paused = True
    return paused


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
        # Kept open only while a verification code is outstanding. Separate from
        # the caller's API session so accounts cookies stay here.
        self._login_session: aiohttp.ClientSession | None = None
        self._pending_login: _PendingVerification | None = None
        if tokens:
            self._set_tokens(tokens)

    # --- lifecycle ---

    async def __aenter__(self) -> ButterflyMXClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session if this client created it."""
        try:
            await self._discard_login_attempt()
        finally:
            # Accounts cleanup re-raises CancelledError. The owned API session
            # still has to close, and the cancellation still propagates.
            session = self._session
            self._session = None
            if self._owns_session and session is not None and not session.closed:
                await session.close()

    async def _discard_login_attempt(self) -> None:
        """Drop a half-finished login, including its accounts cookies.

        A cancelled task would otherwise fail the `await session.close()` and
        leave the socket open. Pending cancellation is lifted until close
        finishes; the caller's CancelledError still propagates.
        """
        self._pending_login = None
        session = self._login_session
        self._login_session = None
        if session is None or session.closed:
            return
        # cancelling()/uncancel() arrived in 3.11. 3.10 is still supported, and
        # on 3.10 a caught CancelledError does not cancel the next await.
        # A cancellation that arrives during close() is finished, then re-raised
        # so client.close() and login() do not report success.
        task = asyncio.current_task()
        cancelled = _pause_cancellation(task)
        try:
            await session.close()
        except asyncio.CancelledError:
            cancelled = True
            _pause_cancellation(task)
            if not session.closed:
                await session.close()
        if cancelled:
            raise asyncio.CancelledError

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
            ButterflyMXVerificationRequired: the site asked for a verification code.
                Submit it with `submit_verification_code` on this same client.
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
            # A code challenge is already in progress. Starting another login
            # would close that accounts session.
            if self._pending_login is not None and self._pending_login.action:
                raise self._pending_verification_required()
            if self.refresh_token and await self._refresh_access_token():
                return
            await self._full_login()

    async def _refresh_access_token(self) -> bool:
        """Try the refresh token. Returns False if the server rejected it."""
        _LOGGER.debug("Refreshing access token")
        # Keep the accounts site's cookies out of a caller-provided session
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
            status, text, _ = await self._request(
                "POST",
                f"{self.BASE_URL}/oauth/token",
                session=session,
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

    async def submit_verification_code(self, code: str) -> None:
        """Finish a login that raised `ButterflyMXVerificationRequired`.

        Posts the code on the same accounts session that accepted the password,
        then follows the redirects through to the OAuth token exchange.
        Shares the auth lock with `ensure_token`, so another request cannot
        start a fresh login and close this session mid-submit.
        """
        async with self._auth_lock:
            pending = self._pending_login
            session = self._login_session
            if pending is None or session is None or session.closed or not pending.action:
                raise ButterflyMXApiError("No verification login is in progress")
            _LOGGER.debug("Submitting verification code")
            data = dict(pending.fields)
            data[pending.code_field] = code
            keep_session = False
            try:
                status, text, headers = await self._request(
                    "POST",
                    self._absolute_url(pending.action),
                    session=session,
                    allow_redirects=False,
                    data=data,
                )
                self._raise_for_status(status, text, "Verification")
                await self._complete_login(session, pending, status, text, headers)
            except (ButterflyMXVerificationRequired, ButterflyMXInvalidVerificationCode):
                keep_session = True
                raise
            finally:
                if not keep_session:
                    await self._discard_login_attempt()

    async def resend_verification_code(self) -> None:
        """Ask ButterflyMX to send another code, when the page offers that.

        The phone verification page observed in Nov 2026 has no resend control
        (only "Try a different number"). This uses a resend form or link when
        the code page actually contains one.
        """
        async with self._auth_lock:
            pending = self._pending_login
            session = self._login_session
            if pending is None or session is None or session.closed or not pending.action:
                raise ButterflyMXApiError("No verification login is in progress")
            if not pending.resend_action or not pending.resend_method:
                raise ButterflyMXApiError("This verification step does not offer resend")
            _LOGGER.debug("Requesting another verification code")
            kwargs: dict[str, Any] = {"allow_redirects": False}
            if pending.resend_method == "GET":
                kwargs["params"] = pending.resend_fields or None
            else:
                kwargs["data"] = pending.resend_fields or {}
            # Keep the challenge only when resend returns another code form.
            # A 401/403 or a redirect back to the login page must not leave the
            # client stuck on a session that can no longer be resumed.
            keep_session = False
            try:
                status, text, headers = await self._request(
                    pending.resend_method, self._absolute_url(pending.resend_action), session=session, **kwargs
                )
                self._raise_for_status(status, text, "Resend verification code")
                for _ in range(5):
                    location = headers.get("Location") if status in _REDIRECT_STATUSES else None
                    if location and location.startswith(self.REDIRECT_URI):
                        await self._exchange_code(
                            session, self._authorization_code(location, pending.state), pending.verifier
                        )
                        return
                    if not location:
                        break
                    status, text, headers = await self._request(
                        "GET", self._absolute_url(location), session=session, allow_redirects=False
                    )
                    self._raise_for_status(status, text, "Resend verification code")
                form = _parse_verification_form(text)
                if form is None:
                    raise ButterflyMXApiError("Resend did not return a verification form")
                pending.use_form(form)
                keep_session = True
            finally:
                if not keep_session:
                    await self._discard_login_attempt()

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

        # The login form relies on cookies. Keep them in a session of our own,
        # not the caller's API session. It stays open when a code is required.
        await self._discard_login_attempt()
        # unsafe=True so a test server on an IP address can keep the accounts
        # cookie. Hostname cookies (accounts.butterflymx.com) are stored either way.
        session = aiohttp.ClientSession(
            headers={"User-Agent": self.USER_AGENT},
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )
        self._login_session = session
        pending = _PendingVerification(verifier=verifier, state=state, action="", code_field="code", fields={})
        self._pending_login = pending
        # Closed on every exit, including CancelledError, unless a code challenge
        # was actually delivered. `except Exception` does not catch cancellation.
        keep_session = False
        try:
            _LOGGER.debug("Fetching login page")
            status, page, _ = await self._request(
                "GET", f"{self.BASE_URL}/oauth/authorize", session=session, params=auth_params
            )
            self._raise_for_status(status, page, "Fetching login page")

            parsed = _parse_login_form(page, self.email, self.password)
            if parsed is None:
                raise ButterflyMXApiError("Could not find authenticity_token or _csrf_token on the login page")
            action, form = parsed

            _LOGGER.debug("Submitting login form")
            status, text, headers = await self._request(
                "POST",
                self._absolute_url(action),
                session=session,
                allow_redirects=False,
                data=form,
            )
            self._raise_for_status(status, text, "Login")

            # The old Rails app answers a successful login with 200 + <turbo-stream location="...">.
            # The current identity service answers with a 303 whose Location is relative
            # (`/oauth/authorize?...` on success, `/login/new` when the password is wrong).
            # A verification code is a 200 page (phone sign-in) or a redirect onto one.
            if status == 200 and "<turbo-stream" in text:
                match = re.search(r'location="([^"]+)"', text)
                if match:
                    _LOGGER.debug("Following Turbo Stream redirect")
                    status, text, headers = await self._request(
                        "GET",
                        self._absolute_url(html.unescape(match.group(1))),
                        session=session,
                        allow_redirects=False,
                    )
                    self._raise_for_status(status, text, "Login redirect")

            await self._complete_login(session, pending, status, text, headers)
        except (ButterflyMXVerificationRequired, ButterflyMXInvalidVerificationCode):
            keep_session = True
            raise
        finally:
            if not keep_session:
                await self._discard_login_attempt()

    async def _complete_login(
        self,
        session: aiohttp.ClientSession,
        pending: _PendingVerification,
        status: int,
        text: str,
        headers: Any,
    ) -> None:
        """Follow login redirects. Raise if the page wants a code; return after the token exchange."""
        for _ in range(10):
            location = headers.get("Location") if status in _REDIRECT_STATUSES else None
            if location and location.startswith(self.REDIRECT_URI):
                await self._exchange_code(session, self._authorization_code(location, pending.state), pending.verifier)
                return
            if not location:
                break
            _LOGGER.debug("Login redirect: HTTP %s to %s", status, urllib.parse.urlparse(location).path)
            status, text, headers = await self._request(
                "GET", self._absolute_url(location), session=session, allow_redirects=False
            )
            self._raise_for_status(status, text, "Login redirect")

        # The identity service (Nov 2026) can finish with a 200 "Returning to app…"
        # page whose link and window.location.replace() carry the app redirect,
        # instead of a 302 to it.
        app_redirect = _app_redirect_from_page(text, self.REDIRECT_URI)
        if app_redirect is not None:
            _LOGGER.debug("Following app redirect from the returning-to-app page")
            await self._exchange_code(session, self._authorization_code(app_redirect, pending.state), pending.verifier)
            return

        form = _parse_verification_form(text)
        if form is not None and _page_says_invalid_code(text):
            pending.use_form(form)
            raise self._verification_error(ButterflyMXInvalidVerificationCode, form)
        if form is not None:
            _LOGGER.debug("Verification code required")
            pending.use_form(form)
            raise self._verification_error(ButterflyMXVerificationRequired, form)
        if _page_says_invalid_password(text):
            raise ButterflyMXAuthError("Login failed: invalid email or password")
        raise ButterflyMXAuthError(
            f"Login failed: no OAuth redirect received (check email/password); {_describe_page(status, text)}"
        )

    def _pending_verification_required(self) -> ButterflyMXVerificationRequired:
        pending = self._pending_login
        assert pending is not None
        form = _VerificationForm(
            action=pending.action,
            code_field=pending.code_field,
            fields=dict(pending.fields),
            resend_method=pending.resend_method,
            resend_action=pending.resend_action,
            resend_fields=None if pending.resend_fields is None else dict(pending.resend_fields),
        )
        error = self._verification_error(ButterflyMXVerificationRequired, form)
        assert isinstance(error, ButterflyMXVerificationRequired)
        return error

    def _verification_error(
        self,
        error_type: type[ButterflyMXVerificationRequired] | type[ButterflyMXInvalidVerificationCode],
        form: _VerificationForm,
    ) -> ButterflyMXVerificationRequired | ButterflyMXInvalidVerificationCode:
        error = error_type(can_resend=form.can_resend)
        error._client = self
        return error

    def _absolute_url(self, location: str) -> str:
        """Resolve a possibly relative login URL against the accounts host.

        Relative Locations (`/login/new`, `/oauth/authorize?...`) are what the
        identity service sends. The `com.butterflymx.oauth://` redirect is
        already absolute and is returned unchanged.
        """
        return urllib.parse.urljoin(f"{self.BASE_URL}/", location)

    def _authorization_code(self, location: str, state: str) -> str:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        if params.get("state", [None])[0] != state:
            raise ButterflyMXAuthError("State mismatch in OAuth redirect")
        code = params.get("code", [None])[0]
        if not code:
            raise ButterflyMXAuthError("No authorization code in OAuth redirect")
        return code

    async def _exchange_code(self, session: aiohttp.ClientSession, code: str, verifier: str) -> None:
        _LOGGER.debug("Exchanging authorization code for token")
        status, text, _ = await self._request(
            "POST",
            f"{self.BASE_URL}/oauth/token",
            session=session,
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
