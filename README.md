# ButterflyMX Python Client
<img src="assets/pymx.png" width="300" />

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

1.  **Requirements**: Python 3.6+
2.  **Dependencies**: Install required packages:
    ```bash
    pip install aiohttp
    ```

## Usage

1.  Open `example.py` and update the credentials in the `if __name__ == "__main__":` block (or use the provided defaults if valid).
2.  Run the script:
    ```bash
    python3 example.py
    ```
    *Note: The script now uses `asyncio`. Ensure you run it in an environment that supports `asyncio` (Python 3.7+).*

3.  **First Run**: The script will perform a full login. It might ask you to copy/paste a URL or code.
4.  **Subsequent Runs**: It will load `tokens.json`.
5.  **Interaction**: The script lists your tenants, doors, recent messages, and call history. It then prompts you to validly unlock a door.

## API Documentation

### `ButterflyMXClient`

The main controller for interacting with the API.

**Initialization**
```python
client = ButterflyMXClient(email, password, token_file="tokens.json")
```
*   `email`: Your login email.
*   `password`: Your login password.
*   `token_file`: (Optional) Path to save/load authentication tokens. Defaults to `"tokens.json"`.

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
