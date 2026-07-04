"""
Loxone WebSocket Client
A Python library for connecting to Loxone Smart Home systems via WebSocket.
"""

from importlib.metadata import PackageNotFoundError, version

from .exceptions import LoxoneException, LoxoneHTTPStatusError, LoxoneRequestError
from .lox_ws_api import LoxWs
from .lxtoken import LxToken

# Single source of truth for the version is pyproject.toml; read it back from
# the installed package metadata instead of duplicating a literal here.
try:
    __version__ = version("loxwebsocket")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["LoxWs", "LoxoneException", "LoxoneHTTPStatusError", "LoxoneRequestError", "LxToken"]
