import asyncio
import json
import os
import stat
import time
from pathlib import Path

import aiohttp
import pytest

from butterflymx import (
    ButterflyMXApiError,
    ButterflyMXAuthError,
    ButterflyMXClient,
    ButterflyMXError,
    ButterflyMXInvalidVerificationCode,
    ButterflyMXVerificationRequired,
)
from butterflymx.client import _app_redirect_from_page, _parse_login_form, _parse_verification_form

from .conftest import EMAIL, PASSWORD

FIXTURE = Path(__file__).parent / "fixtures" / "login_csrf.html"
VERIFICATION_FIXTURE = Path(__file__).parent / "fixtures" / "login_verification.html"


async def test_full_login_gets_and_saves_tokens(make_client, fake, tmp_path):
    client = make_client()
    await client.login()

    assert client.access_token in fake.valid_access_tokens
    assert client.refresh_token in fake.valid_refresh_tokens
    assert client.expires_at > time.time() + 86000
    assert fake.requests == ["authorize", "login", "authorize_redirect", "token:authorization_code"]

    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved == client.tokens


async def test_token_file_is_owner_only(client, tmp_path):
    mode = stat.S_IMODE(os.stat(tmp_path / "tokens.json").st_mode)
    assert mode == 0o600


async def test_wrong_password_raises_auth_error(make_client, fake):
    client = make_client(password="wrong")
    with pytest.raises(ButterflyMXAuthError):
        await client.login()
    assert client.access_token is None
    assert "token:authorization_code" not in fake.requests


async def test_missing_authenticity_token_raises_api_error(make_client, fake):
    fake.login_page_has_token = False
    with pytest.raises(ButterflyMXApiError):
        await make_client().login()
    assert "login" not in fake.requests


def test_csrf_fixture_parses_login_form_not_sso_forms():
    page = FIXTURE.read_text().replace("__CSRF_TOKEN__", "csrf-123").replace(
        "__RETURN_TO__", "/oauth/authorize?client_id=test&amp;state=state-1"
    )
    parsed = _parse_login_form(page, EMAIL, PASSWORD)
    assert parsed is not None
    action, fields = parsed
    assert action == "/login"
    assert fields["_csrf_token"] == "csrf-123"
    assert fields["return_to"] == "/oauth/authorize?client_id=test&state=state-1"
    assert fields["email"] == EMAIL
    assert fields["password"] == PASSWORD
    assert fields["commit"] == "Sign in with email"
    assert "login[email]" not in fields
    assert "sso-csrf-not-used" not in fields.values()


def test_rails_login_form_keeps_nested_field_names():
    page = '<form><input value="csrf-123" name="authenticity_token"></form>'
    parsed = _parse_login_form(page, EMAIL, PASSWORD)
    assert parsed is not None
    action, fields = parsed
    assert action == "/login"
    assert fields["authenticity_token"] == "csrf-123"
    assert fields["login[email]"] == EMAIL
    assert fields["login[password]"] == PASSWORD
    assert "_csrf_token" not in fields


def test_login_form_without_either_token_is_rejected():
    assert _parse_login_form('<form><input name="email"><input name="password"></form>', EMAIL, PASSWORD) is None


async def test_csrf_login_page_posts_return_to_and_gets_tokens(make_client, fake):
    fake.login_page_format = "csrf"
    client = make_client()
    await client.login()

    assert client.access_token in fake.valid_access_tokens
    posted = fake.last_login_form
    assert posted["_csrf_token"] == "csrf-123"
    assert posted["email"] == EMAIL
    assert posted["password"] == PASSWORD
    assert posted["commit"] == "Sign in with email"
    assert posted["return_to"].startswith("/oauth/authorize?")
    assert "login[email]" not in posted
    assert "authenticity_token" not in posted
    assert fake.requests == ["authorize", "login", "authorize_redirect", "token:authorization_code"]


async def test_csrf_wrong_password_raises_auth_error(make_client, fake):
    fake.login_page_format = "csrf"
    with pytest.raises(ButterflyMXAuthError, match="invalid email or password") as exc:
        await make_client(password="wrong").login()
    # The page says the password was wrong. That must stay a plain auth error,
    # not the verification-code subclass and not an API/connection error.
    assert type(exc.value) is ButterflyMXAuthError
    assert fake.requests == ["authorize", "login", "login_error"]
    assert "token:authorization_code" not in fake.requests


def test_verification_fixture_is_the_phone_code_form():
    page = VERIFICATION_FIXTURE.read_text().replace("__CSRF_TOKEN__", "csrf-verify")
    form = _parse_verification_form(page)
    assert form is not None
    assert form.action == "/login/phone/verify"
    assert form.code_field == "code"
    assert form.fields == {"_csrf_token": "csrf-verify"}
    # The live page's only link is "Try a different number", not resend.
    assert form.can_resend is False


async def test_verification_code_is_not_an_auth_error(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired, match="verification code") as exc:
        await client.login()
    # Home Assistant maps ButterflyMXAuthError to "Invalid email or password".
    # A code challenge must not take that path.
    assert isinstance(exc.value, ButterflyMXError)
    assert not isinstance(exc.value, ButterflyMXAuthError)
    assert exc.value.can_resend is False
    assert PASSWORD not in str(exc.value)
    assert "token:authorization_code" not in fake.requests
    assert client._login_session is not None and not client._login_session.closed


async def test_verification_code_completes_login_on_the_same_session(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired) as exc:
        await client.login()
    await exc.value.submit_code("123456")

    assert client.access_token in fake.valid_access_tokens
    assert fake.verify_cookie == "verify-session"
    assert fake.last_verify_form["code"] == "123456"
    assert fake.last_verify_form["_csrf_token"] == "csrf-verify"
    assert fake.requests == ["authorize", "login", "verify", "authorize_redirect", "token:authorization_code"]
    assert client._login_session is None


async def test_wrong_verification_code_can_be_retried(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    with pytest.raises(ButterflyMXInvalidVerificationCode, match="Invalid verification code") as exc:
        await client.submit_verification_code("000000")
    assert type(exc.value) is ButterflyMXInvalidVerificationCode
    assert not isinstance(exc.value, ButterflyMXAuthError)
    assert "000000" not in str(exc.value)
    assert fake.requests.count("verify") == 1

    await exc.value.submit_code("123456")
    assert client.access_token in fake.valid_access_tokens
    assert fake.last_verify_form["_csrf_token"] == "csrf-verify-2"
    assert fake.verify_cookie == "verify-session"


async def test_verification_redirect_is_followed(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    fake.verification_via_redirect = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    assert fake.requests == ["authorize", "login", "verify_page"]
    await client.submit_verification_code("123456")
    assert "token:authorization_code" in fake.requests


async def test_failed_resend_allows_a_fresh_login(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    fake.verification_offers_resend = True
    fake.resend_status = 401
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    with pytest.raises(ButterflyMXAuthError):
        await client.resend_verification_code()
    assert client._pending_login is None
    assert client._login_session is None

    fake.resend_status = None
    fake.login_requires_verification = False
    await client.login()
    assert client.access_token in fake.valid_access_tokens
    assert fake.count("authorize") == 2


async def test_resend_is_used_only_when_the_page_offers_it(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    with pytest.raises(ButterflyMXApiError, match="does not offer resend"):
        await client.resend_verification_code()

    fake.verification_offers_resend = True
    with pytest.raises(ButterflyMXVerificationRequired) as exc:
        await make_client().login()
    assert exc.value.can_resend is True
    await exc.value.resend()
    await exc.value.submit_code("123456")
    assert "resend" in fake.requests
    assert fake.last_verify_form["_csrf_token"] == "csrf-verify-resent"


async def test_api_call_does_not_replace_a_pending_verification(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    fake.requests.clear()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.get_tenants()
    assert fake.requests == []
    await client.submit_verification_code("123456")
    assert client.access_token in fake.valid_access_tokens


async def test_api_call_waits_for_verification_submit(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    fake.verify_delay = 0.3
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    submit = asyncio.create_task(client.submit_verification_code("123456"))
    await asyncio.sleep(0.05)
    tenants = await client.get_tenants()
    await submit
    assert tenants
    assert fake.count("authorize") == 1
    assert fake.count("verify") == 1


async def test_close_closes_api_session_when_accounts_cleanup_is_cancelled(make_client):
    client = make_client()
    api = client._get_session()
    login = aiohttp.ClientSession()
    client._login_session = login
    original = client._discard_login_attempt

    async def discard():
        session = client._login_session
        client._login_session = None
        client._pending_login = None
        if session is not None and not session.closed:
            await session.close()
        raise asyncio.CancelledError

    client._discard_login_attempt = discard
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.close()
    finally:
        client._discard_login_attempt = original
    assert login.closed
    assert api.closed
    assert client._session is None


async def test_cancellation_during_session_close_is_reraised(make_client, monkeypatch):
    client = make_client()
    session = aiohttp.ClientSession()
    client._login_session = session
    real_close = aiohttp.ClientSession.close
    calls = {"n": 0}

    async def slow_once(self):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(1)
        await real_close(self)

    monkeypatch.setattr(aiohttp.ClientSession, "close", slow_once)
    task = asyncio.create_task(client._discard_login_attempt())
    await asyncio.sleep(0.05)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, 0.05)
    assert session.closed
    assert client._login_session is None
    assert client._pending_login is None


async def test_login_cleanup_without_python_311_cancel_methods(make_client, monkeypatch):
    """3.10 Task has no cancelling()/uncancel(). Cleanup must still close the session."""

    class _Py310Task:
        pass

    monkeypatch.setattr(asyncio, "current_task", lambda: _Py310Task())
    client = make_client()
    session = aiohttp.ClientSession()
    client._login_session = session
    client._pending_login = object()
    await client._discard_login_attempt()
    assert session.closed
    assert client._login_session is None
    assert client._pending_login is None


async def test_cancelled_login_closes_the_accounts_session(make_client, fake, monkeypatch):
    closed = []
    real_close = aiohttp.ClientSession.close

    async def tracking_close(self):
        closed.append(self)
        await real_close(self)

    monkeypatch.setattr(aiohttp.ClientSession, "close", tracking_close)
    fake.authorize_delay = 1
    client = make_client()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.login(), 0.05)
    assert closed and closed[0].closed
    assert client._login_session is None
    assert client._pending_login is None

    # Cancellation while following the post-password redirect, inside _complete_login.
    fake.authorize_delay = 0
    fake.login_page_format = "csrf"
    fake.login_error_delay = 1
    client = make_client(password="wrong")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.login(), 0.2)
    assert client._login_session is None
    assert client._pending_login is None
    assert closed[-1].closed


async def test_submit_without_a_pending_code_raises(make_client):
    client = make_client()
    with pytest.raises(ButterflyMXApiError, match="No verification login is in progress"):
        await client.submit_verification_code("123456")


async def test_state_mismatch_aborts_login(make_client, fake):
    fake.redirect_state_override = "attacker-state"
    with pytest.raises(ButterflyMXAuthError, match="State mismatch"):
        await make_client().login()
    assert "token:authorization_code" not in fake.requests


async def test_nonce_and_state_are_random_per_login(make_client, fake):
    await make_client(token_file=None).login()
    await make_client(token_file=None).login()
    states = list(fake._pending)
    assert len(states) == 2 and states[0] != states[1]


async def test_valid_token_skips_network(client, fake):
    fake.requests.clear()
    await client.login()
    assert fake.requests == []


async def test_expiring_token_is_refreshed(client, fake):
    old_access, old_refresh = client.access_token, client.refresh_token
    client.expires_at = time.time() + 30  # inside the 60s buffer
    fake.requests.clear()

    await client.login()

    assert fake.requests == ["token:refresh_token"]
    assert client.access_token != old_access
    assert client.refresh_token != old_refresh  # rotated
    assert old_refresh not in fake.valid_refresh_tokens


async def test_refresh_keeps_refresh_token_when_not_rotated(client, fake):
    fake.rotate_refresh_tokens = False
    old_refresh = client.refresh_token
    client.expires_at = 0
    await client.login()
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

    await client.login()

    assert fake.requests[0] == "token:refresh_token"
    assert "token:authorization_code" in fake.requests
    assert client.refresh_token in fake.valid_refresh_tokens


async def test_token_file_is_loaded_lazily(client, make_client, fake):
    second = make_client(password="not-needed")
    assert second.access_token is None  # constructor does no file I/O

    fake.requests.clear()
    await second.login()
    assert second.access_token == client.access_token
    assert fake.requests == []


async def test_corrupt_token_file_is_ignored(make_client, tmp_path):
    (tmp_path / "tokens.json").write_text("{not json")
    client = make_client()
    await client.login()
    assert client.access_token


async def test_no_token_file_by_default():
    client = ButterflyMXClient("e", "p")
    assert client.token_file is None
    await client.close()


async def test_no_token_file_keeps_tokens_in_memory(make_client, tmp_path):
    client = make_client(token_file=None)
    await client.login()
    assert not (tmp_path / "tokens.json").exists()


async def test_tokens_param_skips_login(client, make_client, fake):
    second = make_client(token_file=None, tokens=client.tokens)
    fake.requests.clear()
    await second.login()
    assert fake.requests == []


async def test_on_tokens_updated_sync_callback(make_client):
    seen = []
    client = make_client(token_file=None, on_tokens_updated=seen.append)
    await client.login()
    client.expires_at = 0
    await client.login()
    assert len(seen) == 2
    assert seen[-1] == client.tokens


async def test_on_tokens_updated_async_callback(make_client):
    seen = []

    async def save(tokens):
        seen.append(tokens)

    client = make_client(token_file=None, on_tokens_updated=save)
    await client.login()
    assert seen == [client.tokens]


async def test_client_id_override(make_client, fake):
    client = make_client(token_file=None, client_id="custom-id")
    assert client.CLIENT_ID == "custom-id"
    assert ButterflyMXClient.CLIENT_ID != "custom-id"  # class default untouched
    await client.login()
    assert next(iter(fake._pending.values()))[1] == "custom-id"


async def test_password_not_written_to_token_file(client, tmp_path):
    assert PASSWORD not in (tmp_path / "tokens.json").read_text()


TWO_FACTOR_FIXTURE = Path(__file__).parent / "fixtures" / "login_two_factor_sms.html"
RETURNING_FIXTURE = Path(__file__).parent / "fixtures" / "oauth_returning_to_app.html"


def test_two_factor_sms_page_is_a_code_form_with_resend():
    """The Nov 2026 email/password two-factor page: a code form plus resend."""
    form = _parse_verification_form(TWO_FACTOR_FIXTURE.read_text())
    assert form is not None
    assert form.action == "/login/two_factor_authentication"
    assert form.code_field == "code"
    assert form.fields.get("type") == "sms"
    assert "_csrf_token" in form.fields
    assert form.can_resend is True


def test_returning_to_app_page_yields_the_app_redirect():
    page = RETURNING_FIXTURE.read_text().replace("{code}", "abc").replace("{state}", "xyz")
    url = _app_redirect_from_page(page, ButterflyMXClient.REDIRECT_URI)
    assert url == "com.butterflymx.oauth://oauth?code=abc&state=xyz"
    assert _app_redirect_from_page("<p>nothing here</p>", ButterflyMXClient.REDIRECT_URI) is None
    assert _app_redirect_from_page(
        '<a href="com.butterflymx.oauth://oauth?error=access_denied">x</a>', ButterflyMXClient.REDIRECT_URI
    ) is None


async def test_code_then_returning_to_app_page_completes_login(make_client, fake):
    """The real Nov 2026 sequence: password, SMS code, then a 200 returning-to-app page."""
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    fake.app_redirect_page = True
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    await client.submit_verification_code("123456")
    assert client.access_token in fake.valid_access_tokens
    assert "token:authorization_code" in fake.requests
    assert client._login_session is None


async def test_returning_to_app_page_with_wrong_state_is_rejected(make_client, fake):
    fake.login_page_format = "csrf"
    fake.login_requires_verification = True
    fake.app_redirect_page = True
    fake.redirect_state_override = "someone-else"
    client = make_client()
    with pytest.raises(ButterflyMXVerificationRequired):
        await client.login()
    with pytest.raises(ButterflyMXAuthError, match="State mismatch"):
        await client.submit_verification_code("123456")
