#!/usr/bin/env python3
"""
Example usage of Lox WebSocket Client Library
Demonstrates how to connect to a Loxone server and handle events.
"""

import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def example_connection():
    """Example of how to connect to a Loxone server."""
    
    try:
        from loxwebsocket.lox_ws_api import LoxWs
        
        # Create WebSocket API instance
        ws_api = LoxWs()
        
        # Example connection parameters
        # Replace these with your actual Loxone server details
        LOXONE_URL = "http://192.168.X.XX"
        USERNAME = "XXX"
        PASSWORD = "XXX"
        
        print("🔌 Connecting to Loxone server...")
        print(f"URL: {LOXONE_URL}")
        print(f"Username: {USERNAME}")
        
        # Connect to the server
        await ws_api.connect(
            user=USERNAME,
            password=PASSWORD,
            loxone_url=LOXONE_URL,
            receive_updates=True,
            max_reconnect_attempts=5
        )
        
        print("✅ Connected successfully!")
        
        # Example: Add a message callback to handle incoming messages
        async def on_message(data, message_type):
            print(f"📨 Received message type {message_type}: {data}")
        
        ws_api.add_message_callback(on_message, message_types=[0, 1, 2, 3, 6])
        
        # Example: Add event callbacks
        def on_connected():
            print("🔗 Connection established")
        
        def on_disconnected():
            print("🔌 Connection lost")
        
        ws_api.add_event_callback(on_connected, event_types=[ws_api.EventType.CONNECTED])
        ws_api.add_event_callback(on_disconnected, event_types=[ws_api.EventType.CONNECTION_CLOSED])
        
        # Keep the connection alive for a while
        print("⏰ Keeping connection alive for 30 seconds...")
        await asyncio.sleep(30)
        
        # Example: Send a command to a device
        # device_uuid = "your-device-uuid"
        # await ws_api.send_websocket_command(device_uuid, "On")
        
        # Disconnect
        print("🔌 Disconnecting...")
        await ws_api.stop()
        
        print("✅ Example completed successfully!")
        
    except Exception as e:
        print(f"❌ Connection example failed: {e}")
        logger.exception("Connection example error")

async def example_token_management():
    """Example of token management."""
    
    try:
        from loxwebsocket import LxToken
        
        print("\n🔑 Token Management Example:")
        
        # Create a token
        token = LxToken()
        
        # Set token properties
        token.set_token("example_token_12345")
        token.set_valid_until(1234567890)
        token.set_hash_alg("SHA256")
        
        print(f"Token: {token.token}")
        print(f"Valid until: {token.valid_until}")
        print(f"Hash algorithm: {token.hash_alg}")
        print(f"Seconds to expire: {token.get_seconds_to_expire()}")
        
        print("✅ Token management example completed!")
        
    except Exception as e:
        print(f"❌ Token management example failed: {e}")

async def example_error_handling():
    """Example of error handling."""
    
    try:
        from loxwebsocket import LoxoneException, LoxoneHTTPStatusError, LoxoneRequestError
        
        print("\n🚨 Error Handling Example:")
        
        # Example of catching different types of exceptions
        try:
            raise LoxoneException("This is a test exception")
        except LoxoneException as e:
            print(f"Caught LoxoneException: {e}")
        
        try:
            raise LoxoneHTTPStatusError("HTTP 404 error")
        except LoxoneHTTPStatusError as e:
            print(f"Caught LoxoneHTTPStatusError: {e}")
        
        try:
            raise LoxoneRequestError("Request timeout")
        except LoxoneRequestError as e:
            print(f"Caught LoxoneRequestError: {e}")
        
        print("✅ Error handling example completed!")
        
    except Exception as e:
        print(f"❌ Error handling example failed: {e}")

async def main():
    """Run all examples."""
    print("🚀 Lox WebSocket Client Library Examples")
    print("=" * 50)
    
    # Run examples that don't require actual connection
    await example_token_management()
    await example_error_handling()
    
    print("\n" + "=" * 50)
    print("📝 Connection Example:")
    print("To test actual connection, modify the example_connection() function")
    print("with your real Loxone server details and uncomment the connection code.")
    print("\nExample usage:")
    print("1. Update LOXONE_URL, USERNAME, and PASSWORD in example_connection()")
    print("2. Uncomment the connection code")
    print("3. Run: python example_usage.py")

    await example_connection()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Examples interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        logger.exception("Example error") 