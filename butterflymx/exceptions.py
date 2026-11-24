"""Exceptions raised by the ButterflyMX client."""

from typing import Any


class ButterflyMXError(Exception):
    """Base class for all ButterflyMX client errors."""


class ButterflyMXAuthError(ButterflyMXError):
    """Login failed or the account's tokens were rejected.

    Usually means a wrong email/password, or the session was revoked.
    Retrying won't help until the credentials are fixed.
    """


class _ResumableVerification(ButterflyMXError):
    """A login that is waiting on a verification code.

    The accounts cookies stay on the client that raised this. They are not
    copied into the message, so logging the exception does not record the
    code, password, or session.
    """

    def __init__(self, message: str, *, can_resend: bool = False) -> None:
        super().__init__(message)
        self.can_resend = can_resend
        self._client: Any = None

    async def submit_code(self, code: str) -> None:
        """Finish this login. Same as `client.submit_verification_code`."""
        if self._client is None:
            raise ButterflyMXApiError("No verification login is in progress")
        await self._client.submit_verification_code(code)

    async def resend(self) -> None:
        """Request another code, when the page offers it."""
        if self._client is None:
            raise ButterflyMXApiError("No verification login is in progress")
        await self._client.resend_verification_code()


class ButterflyMXVerificationRequired(_ResumableVerification):
    """The password was accepted, but ButterflyMX asked for a verification code.

    This is not a subclass of ButterflyMXAuthError. Callers that treat every
    auth error as a wrong password (the Home Assistant integration does) must
    be able to tell the two apart.

    The same client still holds the accounts session:

        try:
            await client.login()
        except ButterflyMXVerificationRequired as pending:
            await client.submit_verification_code(code)
            # or: await pending.submit_code(code)
    """

    def __init__(
        self, message: str = "ButterflyMX asked for a verification code", *, can_resend: bool = False
    ) -> None:
        super().__init__(message, can_resend=can_resend)


class ButterflyMXInvalidVerificationCode(_ResumableVerification):
    """The verification code was rejected or has expired.

    When the code form is still on screen, the same client can submit another
    code. `can_resend` is true when that form offers a resend control.
    """

    def __init__(self, message: str = "Invalid verification code", *, can_resend: bool = False) -> None:
        super().__init__(message, can_resend=can_resend)


class ButterflyMXConnectionError(ButterflyMXError):
    """Couldn't reach ButterflyMX: network error, timeout, or a 5xx response.

    Usually temporary; safe to retry later.
    """


class ButterflyMXApiError(ButterflyMXError):
    """ButterflyMX returned something unexpected (GraphQL errors, unexpected
    status codes, or a changed login page)."""
