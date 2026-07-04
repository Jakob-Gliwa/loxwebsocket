# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-07-04

Major stability, protocol-compliance, and tooling release. The client has been
comprehensively hardened (auth/tokens, encryption, reconnect, event system),
gains a full test suite for the first time, and ships a modernized CI/CD pipeline.

### Fixed
- **Salt rotation:** salt/encryption state is now reset correctly on
  re-establish; `ZeroBytePadding` handling corrected.
- **Session leak:** an `aiohttp.ClientSession` that was not closed on teardown
  (and on each failed reconnect attempt) is now always closed.
- **Token refresh:** fixed multiple issues in the refresh path; refresh now runs
  ahead of expiry (`TOKEN_REFRESH_SECONDS_BEFORE_EXPIRY`) instead of racing the
  deadline.
- **Token reset on reconnect:** reconnect now resets the real `self._token`
  (previously wrote to a dead `self.token` attribute, so reconnects kept using
  an invalid token).
- **Expiry calculation:** `get_seconds_to_expire` now uses a UTC epoch
  (timezone bug fixed).
- **Close handling:** on a normal WebSocket close the type of the last *regular*
  message was mistakenly evaluated; the handler now relies on
  `self._ws.close_code`.
- **Stale background tasks on reconnect:** `keep_alive`, `ws_listen` and
  `refresh_token` from a dropped connection are now cancelled immediately at
  teardown instead of only inside the next successful `start()`. `keep_alive`
  additionally re-checks the connection state after its sleep before writing.
  This closes a window where a stale task could wake mid-sleep, write to the
  already-closed socket, be misreported as a fresh disconnect (inflating
  disconnect counts) and race the new handshake.
- **`send_command`:** fixed concurrency/correlation clashes with parallel
  commands; responses are routed through the listener instead of a second
  concurrent `receive()`.
- **LL status code:** both `Code` and `code` are handled (Loxone is
  inconsistent about the casing).
- **Reconnect logging:** fixed off-by-one / unbounded / misleading output; the
  unlimited case is now labelled as such.
- **Logging in general:** corrected wrong close-code messages, replaced the
  `http_ping` `print` with the logger, removed raw byte escapes from logs,
  removed a no-op lock, and fixed a token typo.

### Added
- New event system (`EventType`: `INITIALIZED`, `CONNECTED`,
  `CONNECTION_CLOSED`, `RECONNECTED`, `ANY`) with clean callback registration.
- First comprehensive test suite under `tests/` with unit, integration,
  property-based (Hypothesis), and optional live tests against a real
  Miniserver, plus test helpers (fake WebSocket, Loxone message builders,
  crypto vectors).
- `.env` support (via `python-dotenv`) for local credentials in the example and
  tests.
- New `tests` CI workflow: pytest matrix (Python 3.10–3.13) plus a `ruff` +
  `mypy` gate.

### Changed
- **Protocol compliance:** `Sec-WebSocket-Protocol: remotecontrol` is now set on
  connect.
- **URL normalization:** `http(s)://` is rewritten to `ws(s)://` only in the
  scheme prefix, so hostnames containing "http"/"https" are no longer corrupted.
- Full type annotations across the client; `mypy` gate in CI.
- Project-wide linting/formatting with `ruff`.
- `requires-python >= 3.10`; updated `aiohttp`, `orjson`, `pycryptodome`,
  `py-cpuinfo` and dev/test tooling.
- **Faster CI builds:** aarch64 Linux wheels are built natively on
  `ubuntu-24.04-arm` instead of emulated via QEMU; `build[uv]` build frontend.
- All GitHub Actions bumped to current majors (checkout v7, setup-python v6,
  upload-artifact v7, download-artifact v8, setup-qemu-action v4,
  setup-uv v8.2.0).
- TestPyPI publish runs only after a successful `tests` run (`workflow_run`
  gate), builds exactly the tested commit, derives its version from
  `pyproject.toml` (`<version>.dev<run_number>`), and uses `skip-existing`.

### Platforms
Wheels for Linux (x86_64, aarch64), Windows (AMD64) and macOS (x86_64, arm64),
CPython 3.10–3.13.

[0.6.0]: https://github.com/Jakob-Gliwa/loxwebsocket/releases/tag/v0.6.0
