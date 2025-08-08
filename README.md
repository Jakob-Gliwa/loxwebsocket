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
from loxwebsocket.lox_ws_api import LoxWs

async def main():
    # Create WebSocket API instance
    ws_api = LoxWs()
    
    # Connect to the Miniserver
    await ws_api.connect(
        user="your-username",
        password="your-password",
        loxone_url="http://your-miniserver-ip",
        receive_updates=True,
        max_reconnect_attempts=5
    )

    # Your code here

    # Disconnect
    await ws_api.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Event subscription

The client allows you to subscribe to connection and message events for real-time updates.

### Connection events

```python
import asyncio
from loxwebsocket.lox_ws_api import LoxWs

async def main():
    # Create WebSocket API instance
    ws_api = LoxWs()
    
    # Define event callbacks
    def on_connected():
        print("Connected!")
    
    def on_closed():
        print("Connection closed!")
    
    # Subscribe to connection events
    ws_api.add_event_callback(on_connected, event_types=[ws_api.EventType.CONNECTED])
    ws_api.add_event_callback(on_closed, event_types=[ws_api.EventType.CONNECTION_CLOSED])

    # Establish connection
    await ws_api.connect(
        user="your-username",
        password="your-password",
        loxone_url="http://your-miniserver-ip",
        receive_updates=True
    )

    # Keep the connection alive for demo
    await asyncio.sleep(60)
    
    # Disconnect
    await ws_api.stop()

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
from loxwebsocket.lox_ws_api import LoxWs

async def main():
    # Create WebSocket API instance
    ws_api = LoxWs()
    
    # Define message callbacks
    async def on_control_update(data, message_type):
        print("Control update:", data)
    
    async def on_value_update(data, message_type):
        print("Value update:", data)
    
    async def on_text_update(data, message_type):
        print("Text update:", data)
    
    async def on_keepalive(data, message_type):
        print("Keepalive received")
    
    # Subscribe to message types
    ws_api.add_message_callback(on_control_update, message_types=[0])
    ws_api.add_message_callback(on_value_update, message_types=[2])
    ws_api.add_message_callback(on_text_update, message_types=[3])
    ws_api.add_message_callback(on_keepalive, message_types=[6])

    await ws_api.connect(
        user="your-username",
        password="your-password",
        loxone_url="http://your-miniserver-ip",
        receive_updates=True
    )

    await asyncio.sleep(60)
    
    # Disconnect
    await ws_api.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Sending commands

```python
# Send a command to a device
await ws_api.send_websocket_command(
    device_uuid="your-device-uuid",
    value="1"  # or "0" for off
)

# Send a secured command (requires visualization password)
await ws_api.send_websocket_command_to_visu_password_secured_control(
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
git clone https://github.com/Jakob-Gliwa/loxwebsocket.git
cd loxwebsocket
pip install -e .[dev]
```

## Building

This package includes Cython extensions for optimal performance. The build process automatically detects your platform and compiles appropriate optimized versions.

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.