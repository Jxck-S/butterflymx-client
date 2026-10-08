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

*   **`async login() -> bool`**
    *   Performs the login flow.
    *   Checks for an existing valid token in `tokens.json`.
    *   If invalid, tries to refresh using the refresh token.
    *   If refresh fails, performs a full OAuth PKCE login (scraping login page, handling callbacks).
    *   Returns `True` if authenticated, `False` otherwise.

*   **`async get_tenants() -> list`**
    *   Fetches the list of tenants associated with the account.
    *   Returns a list of dictionaries containing tenant `id` and `name`.

*   **`async get_doors(tenant_id: str) -> list`**
    *   Fetches accessible doors (access points) for a specific tenant.
    *   Returns a list of dictionaries with door details (`id`, `name`, `online` status, `building` name).

*   **`async get_messages(tenant_id: str) -> List[Message]`**
    *   Fetches text messages for the tenant.
    *   Returns a list of `Message` objects.

*   **`async get_calls(tenant_id: str) -> List[Call]`**
    *   Fetches the intercom call history for the tenant.
    *   Returns a list of `Call` objects (including missed calls, visitor calls, etc.).

### `Door`

**Methods**
*   **`async open() -> bool`**
    *   Sends a request to unlock this door.
    *   Returns `True` if successful.

### `Tenant`

**Methods**
*   **`async get_doors() -> List[Door]`**
    *   Fetches accessible doors for this tenant.
*   **`async get_messages() -> List[Message]`**
    *   Fetches text messages for this tenant.
*   **`async get_calls() -> List[Call]`**
    *   Fetches call history for this tenant.

### `ButterflyMXClient`

**Methods**
*   **`async get_tenants() -> List[Tenant]`**
    *   Fetches the list of tenants associated with the account.

A simple data class representing a text message.

**Attributes**
*   `id`: Unique message ID.
*   `body`: Content of the message.
*   `created_at`: Timestamp string.
*   `source`: Name of the sender/device (e.g., "Elevator 4").

### `Call`

A data class representing an intercom call.

**Attributes**
*   `id`: Unique call ID.
*   `logged_at`: Timestamp of the call.
*   `status`: e.g., "CONNECTED", "MISSED".
*   `type`: e.g., "VISITOR".
*   `device`: Name of the intercom device (e.g., "Front Main Lobby").
*   `image_url`: URL to a snapshot image of the caller.

## File Structure
*   `butterflymx/`:
    *   `client.py`: Main library code (`ButterflyMXClient`).
    *   `utils.py`: Helper functions.
    *   `tenant.py`: `Tenant` class.
    *   `door.py`: `Door` class.
    *   `message.py`: `Message` class.
    *   `call.py`: `Call` class.
    *   `__init__.py`: Exports all classes.
*   `example.py`: Example usage script.
*   `tokens.json`: (Generated) Stores session tokens.
