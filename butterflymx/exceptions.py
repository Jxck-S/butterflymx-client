"""Exceptions raised by the ButterflyMX client."""


class ButterflyMXError(Exception):
    """Base class for all ButterflyMX client errors."""


class ButterflyMXAuthError(ButterflyMXError):
    """Login failed or the account's tokens were rejected.

    Usually means a wrong email/password, or the session was revoked.
    Retrying won't help until the credentials are fixed.
    """


class ButterflyMXConnectionError(ButterflyMXError):
    """Couldn't reach ButterflyMX: network error, timeout, or a 5xx response.

    Usually temporary; safe to retry later.
    """


class ButterflyMXApiError(ButterflyMXError):
    """ButterflyMX returned something unexpected (GraphQL errors, unexpected
    status codes, or a changed login page)."""
