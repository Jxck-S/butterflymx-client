# ButterflyMX Python Client
<img src="assets/pymx.png" width="300" />

> [!WARNING]
> **For educational and personal use only.** This library is an unofficial, independent project. It is not affiliated with, endorsed by, or supported by ButterflyMX. It works by using the same private API as the official mobile app, which can change or stop working at any time without notice.
>
> - Use it only with your own account and only for doors you are authorized to access.
> - You are responsible for complying with ButterflyMX's Terms of Service and your building's policies.
> - This software is provided "as is", without warranty of any kind. The authors are not liable for any damages, account suspensions, or security issues resulting from its use.

A reverse-engineered Python client for the ButterflyMX Intercom API. This client allows you to authenticate, retrieve tenant information, view messages and call history, and remotely unlock doors (access points).

## Features

- **Robust Authentication**: Implements the full OAuth 2.0 PKCE flow, including handling Rails Turbo Stream redirects and custom URL schemes.
- **Token Persistence**: Automatically saves and loads credentials (`tokens.json`) to minimize manual logins. Auto-refreshes expired access tokens.
- **Data Retrieval**:
  - Fetch Tenants and Units
  - Fetch Building Entrances (Doors/Intercoms)
  - Fetch Messages (Text messages from visitors/staff)
  - Fetch Call History (Intercom video calls)
- **Remote Control**: Open/Unlock specific doors.

## Installation

Requires Python 3.10+.

```bash
pip install git+https://github.com/Jxck-S/butterflymx-client.git
```

## Usage

1.  Run the example script and enter your ButterflyMX email and password when prompted:
    ```bash
    python3 example.py
    ```
2.  **First Run**: The script performs a full login and saves tokens to `tokens.json`.
3.  **Subsequent Runs**: It loads `tokens.json` and refreshes the access token as needed.
5.  **Interaction**: The script lists your tenants, doors, recent messages, and call history. It then prompts you to validly unlock a door.

## API Documentation

### `ButterflyMXClient`

The main controller for interacting with the API.

**Initialization**
```python
client = ButterflyMXClient(email, password, token_file="tokens.json", client_id=None)
```
*   `email`: Your login email.
*   `password`: Your login password.
*   `token_file`: (Optional) Path to save/load authentication tokens. Defaults to `"tokens.json"`. Pass `None` to keep tokens in memory only.
*   `client_id`: (Optional) OAuth client ID. Defaults to the official mobile app's public client ID (see below).

**Authentication & token refresh**

Access tokens last about 24 hours. Every request checks the token first. If it is expired or within 60 seconds of expiring, the client refreshes it with the refresh token. If the refresh fails, it logs in again with your email and password. If a request still gets a `401`, the client forces a refresh and retries once.

**About the client ID**

The default `CLIENT_ID` is the public OAuth client ID that the official ButterflyMX mobile app uses. It is not a secret: the login uses PKCE, which is designed for apps that cannot keep secrets, and the ID is visible in the app's network traffic. If ButterflyMX changes it, you can find the new one by intercepting the app's login with a proxy like [mitmproxy](https://mitmproxy.org/) or Proxyman. Look for the `client_id` parameter on the request to `https://accounts.butterflymx.com/oauth/authorize`, then pass it as `client_id=...`.

**Methods**

*   **`async login() -> bool`**: Uses the saved token if it's still valid, otherwise refreshes it, otherwise does a full OAuth PKCE login. Returns `True` if authenticated. You don't need to call this before other methods: every request makes sure the token is valid first.
*   **`async get_tenants() -> list[Tenant]`**: The tenants (units) on this account.
*   **`async query_graphql(query, variables=None) -> dict | None`**: Runs a raw GraphQL query. Returns the response JSON, or `None` on an HTTP error.

### `Tenant`

Attributes: `id`, `name`.

*   **`async get_doors() -> list[Door]`**: Doors (access points) this tenant can open.
*   **`async get_messages() -> list[Message]`**: Text messages, newest first.
*   **`async get_calls() -> list[Call]`**: Intercom call history, newest first.
*   **`async get_access_logs() -> list[Access]`**: Door releases, newest first.

### `Door`

Attributes: `id`, `name`, `online`, `open_duration`, `building_name`, `tenant_id`.

*   **`async open() -> bool`**: Unlocks the door. Returns `True` if successful.

### `Message`

Attributes: `id`, `body`, `created_at`, `source` (device name, e.g. "Front Lobby"), `visitor_name`, `image_url`.

### `Call`

Attributes: `id`, `logged_at`, `status` (e.g. `OPENED_DOOR`, `MISSED`), `type` (e.g. `VISITOR`), `device`, `image_url`.

### `Access`

Attributes: `id`, `logged_at`, `type` (e.g. `TENANT`, `VISITOR`), `method` (e.g. `SWIPE_TO_OPEN`), `door_name`, `device_name`, `image_url`.

## Logging

The library logs through Python's `logging` module under the `butterflymx` logger. To see what it's doing:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Development

The tests run against a local fake ButterflyMX server, so they never touch the real API or need an account.

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

CI runs both on every push and pull request.
