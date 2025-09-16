## Lox WebSocket Client

A Python client for connecting to Loxone Miniserver via WebSocket with optional encryption and token-based authentication.

Adapted from [PyLoxone](https://github.com/JoDehli/PyLoxone).

### Features

- Asynchronous WebSocket client (aiohttp)
- Encrypted communication and secured commands
- High-performance Cython parsers (auto-selected per CPU)
- Token acquisition and refresh
- Message and connection event callbacks

### Installation

```bash
pip install loxwebsocket
```

Using uv:

```bash
uv add loxwebsocket
```

### Quickstart

```python
import asyncio
from loxwebsocket import LoxWs

async def main():
    ws = LoxWs()
    await ws.connect(
        user="your-username",
        password="your-password",
        loxone_url="http://miniserver-ip-or-host",
        receive_updates=True,
        max_reconnect_attempts=5,
    )
    # ...
    await ws.stop()

asyncio.run(main())
```

## API Reference

### loxwebsocket.LoxWs

Client that manages the WebSocket lifecycle, authentication, encryption, message parsing, and callbacks.

Constructor

```python
LoxWs(version: float = 15.0)
```

- version: Miniserver compatibility used for auth hashing selection.

#### Lifecycle

- `await connect(user: str, password: str, loxone_url: str, receive_updates: bool = True, max_reconnect_attempts: int = 0) -> None`
  - Establishes the encrypted WebSocket connection, acquires/uses token, and (optionally) enables status updates.
- `await stop() -> None | int`
  - Closes WebSocket and HTTP session. Returns `-1` on error, otherwise `None`.
- `await http_ping() -> bool`
  - Returns whether the Miniserver base URL is reachable (HTTP 200).

Note: The client performs automatic reconnection when the connection drops.

#### Sending commands

- `await send_websocket_command(device_uuid: str | bytes, value: str) -> None`
  - Sends an immediate command, e.g. "jdev/sps/io/<uuid>/<value>".
- `await send_websocket_command_to_visu_password_secured_control(device_uuid: str, value: str, visu_pw: str) -> None`
  - Sends a command to a visualization-password protected control. The client requests the required salt/key and sends the queued command.

Advanced

- `await send_command(command: str) -> str | bytes`
  - Low-level encrypted request/response helper for Loxone commands.

#### Subscriptions (callbacks)

Message callbacks receive parsed data and the numeric message type. Callbacks must be async callables.

```python
async def on_value_update(data, message_type: int):
    ...

ws.add_message_callback(on_value_update, message_types=[2])
```

- `add_message_callback(callback: Callable[[Any, int], Awaitable[None]], message_types: list[int]) -> None`
  - Registers a coroutine callback for the specified message types.
- `remove_message_callback(callback, message_types: list[int]) -> None`
  - Unregisters a previously added callback.

Message types

- 0: Control/text updates
- 1: Binary payload (not parsed)
- 2: Value updates (high-frequency)
- 3: Text block updates
- 6: Keepalive responses
- 4, 5, 7: Other/unparsed

#### Connection events

Define async callbacks and subscribe to connection lifecycle events via `add_event_callback`.

```python
from loxwebsocket import LoxWs

async def on_connected():
    ...

ws = LoxWs()
ws.add_event_callback(on_connected, event_types=[LoxWs.EventType.CONNECTED])
```

Event enum

- `EventType.ANY`
- `EventType.INITIALIZED`
- `EventType.CONNECTED`
- `EventType.CONNECTION_CLOSED`
- `EventType.RECONNECTED`

API

- `add_event_callback(callback: Callable[[], Awaitable[None]], event_types: list[EventType]) -> None`
  - Registers an event callback for selected events (or `ANY`).

### loxwebsocket.LxToken

Container for token details used by the client. Typically managed internally.

Properties

- `token: str`
- `valid_until: int` (seconds since 2009-01-01 in Loxone epoch)
- `hash_alg: str` (e.g., "SHA1" or "SHA256")
- `get_seconds_to_expire() -> int`

### Exceptions

- `loxwebsocket.exceptions.LoxoneException`: Base package error
- `loxwebsocket.exceptions.LoxoneHTTPStatusError`: Non-OK HTTP response from Miniserver
- `loxwebsocket.exceptions.LoxoneRequestError`: Error during HTTP request

### Performance notes

The client uses Cython-based parsers for high-rate value updates (message type 2). At import time, a CPU capability check selects an optimized or compatible parser.

### Requirements

- Python 3.8+
- aiohttp
- orjson
- pycryptodome
- construct

### Development

```bash
git clone https://github.com/Jakob-Gliwa/loxwebsocket.git
cd loxwebsocket
pip install -e .[dev]
# or using uv
uv pip install -e .[dev]
```

### License

MIT License – see `LICENSE`.

### Contributing

Issues and PRs are welcome.