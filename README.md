# Lox WebSocket Client

A Python library for connecting to Loxone Smart Home systems via WebSocket.

This library was adapted from [PyLoxone](https://github.com/JoDehli/PyLoxone) - thank you for your excellent work!

## Features

- Asynchronous WebSocket communication with Loxone Miniserver
- Encrypted communication support
- High-performance Cython modules for message parsing
- Support for various Loxone data types and structures
- Token-based authentication

## Installation

```bash
pip install loxwebsocket
```

## Usage

```python
import asyncio
from loxwebsocket.lox_ws_api import loxwebsocket

async def main():
    # Connect to the Miniserver
    await loxwebsocket.connect(
        user="your-username",
        password="your-password",
        loxone_url="https://your-miniserver-ip"
    )

    # Your code here

    # Disconnect
    await loxwebsocket.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Event subscription

In addition to a higher-level API, the low-level client exposes a singleton instance `loxwebsocket` you can use to subscribe to connection and message events.

### Connection events

```python
import asyncio
from loxwebsocket.lox_ws_api import loxwebsocket

async def on_connected():
    print("Connected!")

async def on_closed():
    print("Connection closed!")

async def main():
    # Subscribe to connection events
    loxwebsocket.add_event_callback(on_connected, [loxwebsocket.EventType.CONNECTED])
    loxwebsocket.add_event_callback(on_closed, [loxwebsocket.EventType.CONNECTION_CLOSED])

    # Establish connection
    await loxwebsocket.connect(
        user="your-username",
        password="your-password",
        loxone_url="https://your-miniserver-ip"
    )

    # Keep the connection alive for demo
    await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
```

### Message events

You can subscribe to specific Loxone message types to process updates efficiently.

- Type 0: Control/text updates
- Type 2: Value updates
- Type 3: Text block updates
- Type 6: Keepalive responses

```python
import asyncio
from loxwebsocket.lox_ws_api import loxwebsocket

async def on_control_update(data, message_type):
    print("Control update:", data)

async def on_value_update(data, message_type):
    print("Value update:", data)

async def on_text_update(data, message_type):
    print("Text update:", data)

async def on_keepalive(data, message_type):
    print("Keepalive received")

async def main():
    # Subscribe to message types
    loxwebsocket.add_message_callback(on_control_update, [0])
    loxwebsocket.add_message_callback(on_value_update, [2])
    loxwebsocket.add_message_callback(on_text_update, [3])
    loxwebsocket.add_message_callback(on_keepalive, [6])

    await loxwebsocket.connect(
        user="your-username",
        password="your-password",
        loxone_url="https://your-miniserver-ip"
    )

    await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
```

### Sending commands

```python
# Send a command to a device
await loxwebsocket.send_websocket_command(
    device_uuid="your-device-uuid",
    value="1"  # or "0" for off
)

# Send a secured command (requires visualization password)
await loxwebsocket.send_websocket_command_to_visu_password_secured_control(
    device_uuid="your-device-uuid",
    value="1",
    visu_pw="your-visualization-password"
)
```

## Requirements

- Python 3.8+
- aiohttp
- orjson
- pycryptodome
- construct

## Development

To set up for development:

```bash
git clone https://github.com/yourusername/loxwebsocket.git
cd loxwebsocket
pip install -e .[dev]
```

## Building

This package includes Cython extensions for optimal performance. The build process automatically detects your platform and compiles appropriate optimized versions.

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.