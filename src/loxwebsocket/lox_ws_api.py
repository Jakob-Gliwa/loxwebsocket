"""
Adapted from: https://github.com/JoDehli/PyLoxone
Thank you for your work!
"""

import asyncio
import binascii
import enum
import logging
import platform
import queue
import time
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import cpuinfo
import orjson as json
from aiohttp import WSMsgType
from Crypto.Hash import HMAC, SHA1, SHA256

import loxwebsocket.const as c
from loxwebsocket.encryption import LxEncryptionHandler, LxJsonKeySalt
from loxwebsocket.exceptions import LoxoneException
from loxwebsocket.lxtoken import LxToken

_LOGGER = logging.getLogger(__name__)

machine_lower = platform.machine().lower()
# On ARM/aarch64 we deliberately use the compatible extractor. Optimized build targets AVX on x86_64.
if any(arch in machine_lower for arch in ("arm", "aarch64")):
    from loxwebsocket.cython_modules.extractor_compatible import (
        parse_message,
        parse_type_3_message,
    )
    _EXTRACTOR_IMPL = f"compatible (arch={machine_lower})"
else:
    try:
        info = cpuinfo.get_cpu_info() or {}
        flags = {flag.lower() for flag in info.get("flags", [])}
        if "avx" in flags and "avx2" in flags:
            from loxwebsocket.cython_modules.extractor_optimized import (
                parse_message,
                parse_type_3_message,
            )
            _EXTRACTOR_IMPL = "optimized (avx+avx2)"
        else:
            from loxwebsocket.cython_modules.extractor_compatible import (
                parse_message,
                parse_type_3_message,
            )
            _EXTRACTOR_IMPL = "compatible (missing avx/avx2)"
    except Exception as e:  # Fallback on any detection failure
        _LOGGER.warning("CPU feature detection failed (%s). Using compatible extractor.", e)
        from loxwebsocket.cython_modules.extractor_compatible import (
            parse_message,
            parse_type_3_message,
        )
        _EXTRACTOR_IMPL = "compatible (detection failed)"

_LOGGER.info("Extractor in use: %s", _EXTRACTOR_IMPL)

_DEBUG_ENABLED = _LOGGER.isEnabledFor(logging.DEBUG)


def _ll_status_code(ll: dict) -> str | None:
    """Return the status code from an ``LL`` response body.

    Loxone is inconsistent about the casing: command/control responses use
    ``Code`` while token responses (getkey2/gettoken/refreshjwt) use ``code``.
    Accept either so neither path silently mis-handles a response.
    """
    return ll.get("Code", ll.get("code"))


class LoxWs:

    class EventType(enum.IntEnum):
        ANY = 0
        INITIALIZED = 1
        CONNECTED = 2
        CONNECTION_CLOSED = 3
        RECONNECTED = 4

    """Loxone Websocket singleton class."""
    _instance = None
    _initialized = False
    _receive_updates = True
    _event_callbacks: "dict[Callable[[], Coroutine[Any, Any, None]], list[LoxWs.EventType]]" = {}

    def __init__(
        self,
        version=15.0
    ):
        _LOGGER.info("Websocket Client Initializing...")
        if self._initialized:
            return

        self._version = version
        self._initialized = True
        self._token = LxToken()
        self._session_key = None
        self._session = None
        self._ws = None
        self._current_message_type = None
        self._visual_hash = None
        self._message_callbacks = {i: [] for i in range(8)}
        self.background_tasks = set()
        self.state = "CLOSED"
        self._secured_queue = queue.Queue(maxsize=1)
        self._encryption_handler = LxEncryptionHandler()
        self._connect_lock = asyncio.Lock()  # Add a lock for the connect method
        # Request/response commands (send_command) must not call receive()
        # while ws_listen owns the socket - aiohttp forbids concurrent
        # receive() calls. Once the listener runs, it hands the response back
        # via this future; the lock keeps only one command outstanding.
        self._command_lock = asyncio.Lock()
        self._response_future: asyncio.Future | None = None
        self._listener_running = False

        self._message_handler = {
            0: self.extract_type_0_message,
            1: self.extract_type_1_message,
            2: self.extract_type_2_message,
            3: self.extract_type_3_message,
            4: self.extract_other_messages,
            5: self.extract_other_messages,
            6: self.extract_type_6_message,
            7: self.extract_other_messages,
        }

    async def connect(self, user, password, loxone_url, receive_updates=True, max_reconnect_attempts=c.CONNECT_RETRIES):
        async with self._connect_lock:  # Use the lock to ensure single execution
            if not self._initialized:
                return
            self._max_reconnect_attempts = max_reconnect_attempts
            self._receive_updates = receive_updates
            if not self._ws or self._ws.closed or self.state != "CONNECTED":
                self._username = user
                self._password = password
                # aiohttp requires an absolute URL with a scheme; default to
                # plain http:// when the caller passes a bare host/IP.
                if not loxone_url.startswith(("http://", "https://")):
                    loxone_url = f"http://{loxone_url}"
                self._loxone_url = loxone_url
                self._loxone_ws_url = loxone_url.replace("https", "wss") if loxone_url.startswith("https:") else loxone_url.replace("http", "ws")
                await self.async_init()
                await self.start()
                await self.send_event(self.EventType.CONNECTED)

    async def async_init(self):
        """Initialize encryption, connect to Loxone, exchange keys, authenticate."""
        # Close any resources left over from a previous (failed) attempt so
        # repeated reconnect attempts can't leak aiohttp ClientSessions/sockets.
        await self._close_connection_resources()

        # A new session negotiates encryption from scratch. Drop any salt state
        # left over from a previous session, otherwise the first encrypted
        # command would reference a stale salt via "nextSalt/..." that the
        # freshly key-exchanged Miniserver never saw (spurious 401 on reconnect).
        self._encryption_handler.reset_salt()

        self._session_key = await self._encryption_handler.generate_session_key(
            self._username, self._password, self._loxone_url)

        try:
            _LOGGER.debug("Connecting to Websocket with aiohttp...")
            self._session = aiohttp.ClientSession(json_serialize=json.dumps,)
            self._ws = await self._session.ws_connect(
                f"{self._loxone_ws_url}/ws/rfc6455",
                # doc p. 7 step 3b: the Miniserver expects the "remotecontrol"
                # websocket subprotocol.
                protocols=("remotecontrol",),
                timeout=c.TIMEOUT,
                heartbeat=None,
                autoping=False
            )

            _LOGGER.debug("Connection established, CDM-KEY-EXCHANGE starting...")
            await self._ws.send_str(f"{c.CMD_KEY_EXCHANGE}{self._session_key}")

            # 1) wait for session key header
            header_msg = await self._ws.receive()
            if header_msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                await self.parse_loxone_message_header_message(header_msg.data)

            # 2) wait for session key response
            data_msg = await self._ws.receive()
            if data_msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                resp_json = json.loads(data_msg.data)
                if _ll_status_code(resp_json.get("LL", {})) != "200":
                    raise ConnectionError("Session key exchange failed.")
            elif data_msg.type == WSMsgType.CLOSED:
                _LOGGER.debug("Websocket closed during session key exchange.")
                raise ConnectionError("Websocket closed during session key exchange.")
            else:
                raise ValueError("Unexpected Message Type during session key exchange.")

            _LOGGER.debug("ENCRYPTION READY")

            if (
                self._token is None
                or self._token.token == ""
                or self._token.get_seconds_to_expire() < 300
            ):
                await self.acquire_token()
            else:
                _LOGGER.debug("use loaded token...")
                try:
                    await self.use_token()
                except Exception as e:
                    _LOGGER.error("Error using existing token. %s. Trying to acquire new token...", e)
                    await self.acquire_token()

            if self._receive_updates:
                await self.send_command(c.CMD_ENABLE_UPDATES)

            self.state = "CONNECTED"

            return True
        except BaseException:
            # Handshake failed: close the session/ws opened in this attempt
            # before propagating, so the next reconnect attempt starts clean
            # instead of leaking an unclosed ClientSession.
            await self._close_connection_resources()
            raise

    async def start(self) -> None:

        for task in self.background_tasks:
            task.cancel()
        self.background_tasks.clear()

        """Start listening tasks."""
        tasks = [
            asyncio.create_task(self.ws_listen(), name="consumer_task"),
            asyncio.create_task(self.keep_alive(c.KEEP_ALIVE_PERIOD), name="keepalive"),
            asyncio.create_task(self.refresh_token(), name="refresh_token"),
        ]
        for task in tasks:
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

    async def reconnect(self) -> None:
        if self.state == "RECONNECTING":
            return
        await self.stop()
        self._token = LxToken()
        self.state = "RECONNECTING"
        """Reconnect the websocket using a series of attempts."""
        attempt = 0
        limit = self._max_reconnect_attempts if self._max_reconnect_attempts else "unlimited"
        while self._max_reconnect_attempts == 0 or self._max_reconnect_attempts > attempt:
            attempt += 1
            _LOGGER.info("Reconnect attempt %s of %s", attempt, limit)
            _LOGGER.info(f"Waiting for {c.CONNECT_DELAY} seconds before retrying...")
            await asyncio.sleep(c.CONNECT_DELAY)
            # check if the loxone server is reachable
            if not await self.http_ping():
                continue
            try:
                if await self.async_init():
                    _LOGGER.debug("Reconnection successful.")
                    await self.start()
                    await self.send_event(self.EventType.RECONNECTED)
                    break
                else:
                    _LOGGER.debug("Reconnection failed.")
            except Exception as e:
                _LOGGER.error("Reconnection failed: %s", e)
        else:
            _LOGGER.error("All reconnection attempts failed.")
            raise LoxoneException("All reconnection attempts failed.")

    async def http_ping(self):
        try:
            async with aiohttp.ClientSession() as session, session.get(self._loxone_url, timeout=3) as response:
                return response.status == 200
        except Exception as e:
            # Expected while the Miniserver is unreachable during an outage;
            # keep at debug level so the reconnect loop doesn't spam warnings.
            _LOGGER.debug("HTTP reachability check failed: %s", e)
            return False

    async def _close_connection_resources(self) -> None:
        """Close the websocket and HTTP session if open and drop the references.

        Safe to call multiple times. Clearing the references afterwards
        prevents a subsequent ``async_init`` from overwriting (and thereby
        leaking) a session that was never closed.
        """
        # The listener no longer owns the socket after this point, so the next
        # handshake's send_command reads responses directly again.
        self._listener_running = False
        # Wake any command still waiting for its response via the listener; the
        # socket is going away and that response will never arrive.
        if self._response_future is not None and not self._response_future.done():
            self._response_future.set_exception(
                ConnectionError("Websocket closed while waiting for command response.")
            )
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                _LOGGER.debug("Error while closing websocket: %s", e)
            finally:
                self._ws = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception as e:
                _LOGGER.debug("Error while closing session: %s", e)
            finally:
                self._session = None

    async def stop(self) -> int:
        """Close the websocket and the underlying session.

        Returns 0 on success and -1 if closing raised.
        """
        try:
            self.state = "STOPPING"
            await self._close_connection_resources()
            self.state = "CLOSED"
            return 0
        except Exception as e:
            _LOGGER.error(e)
            return -1

    async def keep_alive(self, second: int) -> None:
        """Send keepalive messages to keep the websocket open."""
        try:
            while self.state == "CONNECTED":
                await asyncio.sleep(second)
                await self._ws.send_str("keepalive")
        except Exception as e:
            await self.handle_connection_interrupt(exception=e)

    async def refresh_token(self):
        """Refresh the token periodically.

        Do NOT reset the token before refreshing: refreshing requires hashing
        the still-valid token (see hash_token / protocol doc). The refresh
        response then updates the token's validity (and token) in place.

        This runs as a long-lived background task. Any failure must be reported
        explicitly and the loop kept alive - otherwise the task dies silently
        (its exception is only ever surfaced as a late "never retrieved"
        warning) and the token is never refreshed again.
        """
        while True:
            # Refresh ahead of the actual expiry: refreshjwt only succeeds while
            # the token is still valid (doc p. 28), so refreshing exactly at
            # expiry races the deadline. When a refresh fails, seconds_to_expire
            # keeps shrinking, so this naturally retries every CONNECT_DELAY as
            # the deadline approaches (guard also prevents a busy-spin).
            seconds_to_refresh = self._token.get_seconds_to_expire() - c.TOKEN_REFRESH_SECONDS_BEFORE_EXPIRY
            await asyncio.sleep(max(seconds_to_refresh, c.CONNECT_DELAY))
            try:
                await self._refresh_token()
            except Exception as e:
                _LOGGER.error("Token refresh failed, will retry in %ss: %s", c.CONNECT_DELAY, e)

    async def _refresh_token(self):
        """Refresh the token after it has expired."""
        _LOGGER.debug("Try to refresh token.")
        # Send command to get the key
        token_hash = await self.hash_token()

        cmd = (
            f"{c.CMD_REFRESH_TOKEN if self._version < 10.2 else c.CMD_REFRESH_TOKEN_JSON_WEB}"
            f"{token_hash}/{self._username}"
        )
        message = await self.send_command(cmd)
        await self.handleValidUntilMessage(message)

    async def handleValidUntilMessage(self, message):
        try:
            resp_json = json.loads(message)
            value = resp_json["LL"]["value"]
            if _ll_status_code(resp_json["LL"]) == "200" and value["validUntil"]:
                self._token.set_valid_until(value["validUntil"])
                # A refresh response also carries a *new* token string (and
                # legacy tokens are converted to JWTs on refresh, doc p. 28).
                # Adopt it so subsequent hashing/auth uses the current token
                # instead of the now-superseded pre-refresh one. The plain
                # authwithtoken response has no "token" field -> left untouched.
                new_token = value.get("token")
                if new_token:
                    self._token.set_token(new_token)
            else:
                raise LoxoneException("Error authenticating with token. Unexpected content in Loxone response.")
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            _LOGGER.error("Error authenticating with token. Unexpected content in Loxone response.")
            raise LoxoneException("Error authenticating with token. Unexpected content in Loxone response.") from e

    async def send_command(self, command):
        """Send a command over the websocket and return its response payload.

        During the initial handshake / reconnect the listener task (ws_listen)
        is not consuming the socket yet, so we read the response frames
        (header + payload) directly.

        Once ws_listen owns the socket we must not call receive() concurrently
        (aiohttp raises "Concurrent call to receive() is not allowed"). In that
        case the listener delivers the response to us through
        ``self._response_future`` (see _async_process_message). Commands are
        serialised via ``_command_lock`` so only one response is outstanding.
        """
        enc_command = await self._encryption_handler.encrypt(command)

        if not self._listener_running:
            await self._ws.send_str(enc_command)
            return await self._receive_command_response()

        async with self._command_lock:
            loop = asyncio.get_running_loop()
            self._response_future = loop.create_future()
            try:
                await self._ws.send_str(enc_command)
                return await asyncio.wait_for(self._response_future, timeout=c.TIMEOUT)
            finally:
                self._response_future = None

    async def _receive_command_response(self):
        """Read a single command response (8-byte header + payload) directly.

        Only safe while the listener task is not consuming the socket, i.e.
        during the initial handshake / reconnect before ws_listen starts.
        """
        # 1) wait for header
        header_msg = await self._ws.receive()
        if header_msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
            header = header_msg.data
        elif header_msg.type == WSMsgType.CLOSED:
            raise ConnectionError("Websocket closed while waiting for header.")
        else:
            header = None

        if header:
            await self.parse_loxone_message_header_message(header)

        # 2) wait for actual data
        data_msg = await self._ws.receive()
        if data_msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
            message = data_msg.data
        elif data_msg.type == WSMsgType.CLOSED:
            raise ConnectionError("Websocket closed while waiting for data.")
        else:
            message = None

        return message

    async def send_command_to_visu_password_secured_control(self, device_uuid: str, value: str, visu_pw: str):
        """Send commands to a Loxone Control that is secured by a visualization password."""
        visu_hash = self._encryption_handler.hash_visu_password_secured_command(self._visual_hash, visu_pw)
        command = f"jdev/sps/ios/{visu_hash}/{device_uuid}/{value}"
        enc_command = await self._encryption_handler.encrypt(command)
        await self._ws.send_str(enc_command)

    async def send_websocket_command_to_visu_password_secured_control(
        self, device_uuid: str, value: str, visu_pw: str
    ) -> None:
        """Queue a secured command to be sent later."""
        self._secured_queue.put((device_uuid, value, visu_pw))
        enc_command = await self._encryption_handler.encrypt_visual_command(self._username)
        await self._ws.send_str(enc_command)

    async def send_websocket_command(self, device_uuid: str, value: str) -> None:
        """Send a websocket command to the Miniserver."""
        command = f"jdev/sps/io/{device_uuid.decode() if isinstance(device_uuid, bytes) else device_uuid}/{value}"
        if _DEBUG_ENABLED:
            _LOGGER.debug("send command: %s", command)
        enc_command = await self._encryption_handler.encrypt(command)
        await self._ws.send_str(enc_command)

    async def get_key_salt_for_secure_commands_and_send(self, loxone_json_text_message):
        """
        Check if the returned data is a salt/key for secure commands
        and flush the secured_queue if so.
        """
        key_and_salt = LxJsonKeySalt()
        key_and_salt.read_user_salt_responce(loxone_json_text_message)
        key_and_salt.time_elapsed_in_seconds = round(time.time())
        self._visual_hash = key_and_salt

        while not self._secured_queue.empty():
            secured_message = self._secured_queue.get()
            await self.send_command_to_visu_password_secured_control(
                secured_message[0],
                secured_message[1],
                secured_message[2],
            )

    async def handle_connection_interrupt(self, msg_type: int | None = None, exception: Exception | None = None):
        close_code = self._ws.close_code if self._ws else None
        # Additional context that the close-code classification below cannot
        # convey. The plain CLOSED/CLOSING case is described by the match block,
        # so it is intentionally not logged here to avoid labelling every close
        # (e.g. a normal 1000) as "unexpected".
        if exception:
            _LOGGER.error("Connection error: %s of type %s%s", exception, type(exception), f" with code: {close_code}" if close_code is not None else "")
        elif msg_type == WSMsgType.ERROR:
            _LOGGER.error("Connection error - most likely from listener %s", f" with code: {close_code}" if close_code is not None else "!")

        match close_code:
            # Standard RFC6455 close codes
            case 1000:
                _LOGGER.info("Connection closed normally (code: %s).", close_code)
            case 1001:
                _LOGGER.info("Connection closed: endpoint is going away, e.g. Miniserver shutdown or reboot (code: %s).", close_code)
            case 1005:
                _LOGGER.warning("Connection closed without a status code (code: %s).", close_code)
            case 1006:
                _LOGGER.warning("Connection closed abnormally: lost without a close frame (code: %s).", close_code)
            case 1011:
                _LOGGER.error("Connection closed: the Miniserver encountered an internal error (code: %s).", close_code)
            case 1012:
                _LOGGER.info("Connection closed: the Miniserver is restarting (code: %s).", close_code)
            # Loxone-specific close codes
            case 4003:
                _LOGGER.error("Connection closed: blocked due to too many failed login attempts (code: %s)", close_code)
            case 4004:
                _LOGGER.error("Connection closed: Some user has been changed (code: %s)", close_code)
            case 4005:
                _LOGGER.error("Connection closed: The user currently connected has been changed either by themself or another user (code: %s)", close_code)
            case 4006:
                _LOGGER.error("Connection closed: The user trying to establish a connection has been disabled (code: %s)", close_code)
            case 4007:
                _LOGGER.error("Connection closed: The Miniserver is currently performing an update (code: %s)", close_code)
            case 4008:
                _LOGGER.error("Connection closed: The Miniserver doesn't have any event slots for the initiated WebSocket session (code: %s)", close_code)
            case None:
                _LOGGER.error("Connection closed unexpectedly without a close code.")
            case _:
                _LOGGER.error("Connection closed with unrecognized code: %s", close_code)

        # Notify subscribers that the connection was lost before we start the
        # reconnect loop. Skipped if a reconnect is already in progress so the
        # event isn't emitted twice for the same outage.
        if self.state != "RECONNECTING":
            await self.send_event(self.EventType.CONNECTION_CLOSED)

        await self.reconnect()

    async def ws_listen(self) -> None:
        """
        Listen for websocket messages in a background task.
        """
        # From here on this task owns receive() on the socket. send_command
        # must therefore route responses through us instead of reading itself.
        # Reset happens in _close_connection_resources when the socket tears
        # down, so a fresh listener re-arms the flag without racing this one.
        self._listener_running = True
        try:
            async for msg in self._ws:
                try:
                    await self._async_process_message(msg.data)
                except Exception as inner_exception:
                    _LOGGER.error(f"Error processing message: {inner_exception}")
                    continue
            await self.handle_connection_interrupt(msg_type=msg.type)

        except Exception as e:
            await self.handle_connection_interrupt(exception=e)

    async def _async_process_message(self, message: bytes) -> None:
        """
        Process the incoming Loxone message.
        First check if it's a header, if not parse the content.
        """
        if not await self.parse_loxone_message_header_message(message):
            # Hand command responses back to a waiting send_command. These are
            # request/response text payloads (type 0); value updates (type 2/3)
            # and keepalive (type 6) must never satisfy the future.
            if (
                self._current_message_type == 0
                and self._response_future is not None
                and not self._response_future.done()
            ):
                self._response_future.set_result(message)
                self._current_message_type = None
                return

            parsed_data = await self._message_handler[self._current_message_type](message, {})
            if parsed_data:
                if _DEBUG_ENABLED:
                    _LOGGER.debug("message [type:%s]: %s", self._current_message_type, parsed_data)

                for callback in self._message_callbacks[self._current_message_type]:
                    task = asyncio.create_task(callback(parsed_data, self._current_message_type))
                    task.add_done_callback(lambda t: _LOGGER.error(f"Error in message callback: {t.exception()}") if t.exception() else None)

            self._current_message_type = None

    async def parse_loxone_message_header_message(self, message):
        """
        Parse the 8-byte Loxone message header.
        [0]: fixed 0x03
        [1]: message type
        [2]: info flags
        [3]: reserved
        [4:8]: payload length (uint32, little endian)
        If it's exactly 8 bytes, treat it as a header. Otherwise it's payload.
        """
        if len(message) == 8:
            try:
                self._current_message_type = message[1]
                if _DEBUG_ENABLED:
                    _LOGGER.debug("Current message type:%s", self._current_message_type)
                return True
            except ValueError as err:
                _LOGGER.warning("error parse_loxone_message...")
                raise ValueError(f"error parse_loxone_message:{message}") from err
        return False


    async def extract_type_0_message(self, message, event_dict):
        """Type 0: Text message"""
        try:
            json_message =  json.loads(message)

            if not json_message.get("LL"):
                return None

            code = _ll_status_code(json_message["LL"])
            if code != "200":
                if code == "404" and json_message["LL"].get("control"):
                        #TODO this could potentially flood the logs - maybe add a config to surpess this warning
                        _LOGGER.warning("Unrecognized command or control not found: %s. If you receive all values that you expect and/or don't have a control with this name on your Miniserver, you can ignore this message. If you want to get rid of this message, you can add the control to the whitelist/filter/donotforward in the configuration.", json_message["LL"]["control"])
                return None

            if json_message["LL"].get("value") and not isinstance(json_message["LL"]["value"], str) and json_message["LL"]["value"].get("key") and json_message["LL"]["value"].get("salt"):
                await self.get_key_salt_for_secure_commands_and_send(json_message)
                return None

            if json_message["LL"].get("control"):
                json_message["LL"]["control"] = await self._encryption_handler.decrypt_control_response(json_message["LL"]["control"])
                event_dict[json_message["LL"]["control"].split("/")[-2].encode()] = json_message["LL"]
                return event_dict

            return json_message
        except json.JSONDecodeError as e:
            _LOGGER.debug(f"Error parsing JSON: {e}")
            return message.decode("utf-8") if isinstance(message, bytes) else message

    async def extract_type_1_message(self, message, event_dict):
        """Type 1: Binary file (not further processed here)"""
        return event_dict

    async def extract_type_2_message(self, message, event_dict):
        """
        Type 2: Value updates.
        Uses optimized Cython parser for high-performance message parsing.
        """
        return parse_message(message)

    async def extract_type_3_message(self, message: bytes, event_dict: dict) -> dict:
       return parse_type_3_message(message)

    async def extract_type_6_message(self, message, event_dict):
        """Type 6: Keepalive response."""
        event_dict["keep_alive"] = "received"
        if _DEBUG_ENABLED:
            _LOGGER.debug("Keep alive response received...")
        return event_dict

    async def extract_other_messages(self, message, event_dict):
        """
        We set _current_message_type to 7 because
        4,5,7 are all 'other' in this code path.
        """
        self._current_message_type = 7
        return event_dict

    async def use_token(self):
        """Use an existing token for authentication."""
        token_hash = await self.hash_token()

        cmd = f"{c.CMD_AUTH_WITH_TOKEN}{token_hash}/{self._username}"
        message = await self.send_command(cmd)
        await self.handleValidUntilMessage(message)

    async def hash_token(self):
        """Hash the token using the current key from the miniserver.

        ``jdev/sys/getkey`` returns the HMAC *key* (hex), not the hash
        algorithm. The algorithm is the one the token was issued with
        (stored on the token when it was acquired/refreshed).
        """
        message = await self.send_command(c.CMD_GET_KEY)
        try:
            resp_json = json.loads(message)
            key = resp_json["LL"]["value"]
            hash_alg = SHA1 if self._token.hash_alg == "SHA1" else SHA256
            return HMAC.new(
                    binascii.unhexlify(key),
                    self._token.token.encode("utf-8"),
                    hash_alg,
                ).hexdigest()
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError) as e:
            _LOGGER.error("Error hashing token. Unexpected content in Loxone response.")
            raise LoxoneException("Error hashing token. Unexpected content in Loxone response.") from e

    async def acquire_token(self):
        """Request a new token from the Loxone miniserver."""
        _LOGGER.debug("acquire_token")
        message = await self.send_command(f"{c.CMD_GET_KEY_AND_SALT}{self._username}")

        key_and_salt = LxJsonKeySalt()
        key_and_salt.read_user_salt_responce(message)

        new_hash = self._encryption_handler.hash_credentials(
            key_and_salt, self._password, self._username
        )
        command = (
            f"{c.CMD_REQUEST_TOKEN_JSON_WEB if self._version >= 10.2 else c.CMD_REQUEST_TOKEN}"
            f"{new_hash}/{self._username}/{c.TOKEN_PERMISSION}/edfc5f9a-df3f-4cad-9dddcdc42c732be2/loxinflux"
        )
        message = await self.send_command(command)

        try:
            resp_json = json.loads(message)
            token = resp_json["LL"]["value"]["token"]
            valid_until = resp_json["LL"]["value"]["validUntil"]
            if token and valid_until:
                self._token = LxToken(token,valid_until,key_and_salt.hash_alg)
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            _LOGGER.error("Error acquiring token. Unexpected content in Loxone response.")
            raise LoxoneException("Error acquiring token. Unexpected content in Loxone response.") from e

    def add_message_callback(self, callback, message_types: list[int] | None = None):
        """Add a message callback function with optional message types filter.

        When ``message_types`` is omitted the callback is registered for all
        known message types.
        """
        if message_types is None:
            message_types = list(self._message_callbacks.keys())
        for message_type in message_types:
            self._message_callbacks[message_type].append(callback)

    def add_event_callback(self, callback, event_types:"list[LoxWs.EventType] | None" = None):
        if event_types is None:
            event_types = [self.EventType.ANY]
        self._event_callbacks[callback] = event_types

    async def send_event(self, event_type:EventType):
        """Dispatch a lifecycle event to all subscribed async callbacks.

        Callbacks must be coroutine functions; they are scheduled as tasks.
        """
        for callback, event_types in list(self._event_callbacks.items()):
            if self.EventType.ANY in event_types or event_type in event_types:
                task = asyncio.create_task(callback())
                task.add_done_callback(lambda t: _LOGGER.error("Error in event callback: %s", t.exception()) if t.exception() else None)

    def remove_message_callback(self, callback, message_types:list[int]):
        """Remove a previously registered callback."""
        for message_type in message_types:
            self._message_callbacks[message_type].remove(callback)


loxwebsocket = LoxWs()
