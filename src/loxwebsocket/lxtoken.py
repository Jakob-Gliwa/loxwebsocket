import time
from datetime import datetime, timezone

# Loxone token lifetimes are expressed as "seconds since 1.1.2009 UTC"
# (doc p. 27). Use a fixed UTC epoch instead of strftime("%s") - the latter is
# non-portable (not part of the C standard) and interprets the naive datetime
# as *local* time, skewing the result by the local UTC offset.
_LOXONE_EPOCH = int(datetime(2009, 1, 1, tzinfo=timezone.utc).timestamp())

class LxToken:
    def __init__(self, token="", valid_until=0, hash_alg="SHA1"):
        self._token = token
        self._valid_until = valid_until
        self._hash_alg = hash_alg

    def get_seconds_to_expire(self):
        expiry = _LOXONE_EPOCH + self._valid_until
        return expiry - int(round(time.time()))

    @property
    def token(self):
        return self._token

    @property
    def valid_until(self):
        return self._valid_until

    def set_valid_until(self, value):
        self._valid_until = value

    def set_token(self, token):
        self._token = token

    @property
    def hash_alg(self):
        return self._hash_alg

    def set_hash_alg(self, hash_alg):
        self._hash_alg = hash_alg
