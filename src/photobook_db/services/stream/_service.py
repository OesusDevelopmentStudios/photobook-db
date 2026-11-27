# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import base64
import hashlib
import hmac
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from photobook_db.services.settings import SettingsService
from photobook_db.utils.problems import Problem
from photobook_db.utils.time import as_utc, utc_now

SIGNING_KEY_SETTING = "stream_signing_key"  # made at first use; never shown or sent
LIFETIME = timedelta(hours=12)


@dataclass(frozen=True)
class StreamGrant:
    """What goes into a stream URL's query: t (the login token's id), exp and sig."""

    token_id: int
    expires: int  # Unix time, seconds
    signature: str

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.expires, UTC)

    def query(self) -> str:
        return f"t={self.token_id}&exp={self.expires}&sig={self.signature}"


class StreamService:
    def __init__(self, settings: SettingsService, now: Callable[[], datetime] = utc_now) -> None:
        self._settings = settings
        self._now = now
        self._key_lock = threading.Lock()

    def grant(self, image_id: int, token_id: int) -> StreamGrant:
        """Lets the holder of the URL read that one file, for the user of that login, until it expires."""
        expires = int(self._unix_now() + LIFETIME.total_seconds())
        return StreamGrant(token_id, expires, self._signature(image_id, token_id, expires))

    def check(self, image_id: int, token_id: int, expires: int, signature: str) -> None:
        """Raises 401 not_authenticated for a signature that does not match or has expired. Whether the login still
        exists and may see the file is for the caller to check."""
        if expires <= self._unix_now() or not hmac.compare_digest(
            signature.encode(), self._signature(image_id, token_id, expires).encode()
        ):
            raise Problem(401, "not_authenticated", "Not logged in", "The stream URL is not valid, or has expired.")

    def _unix_now(self) -> float:
        return as_utc(self._now()).timestamp()  # the stored times are UTC without a time zone

    def _signature(self, image_id: int, token_id: int, expires: int) -> str:
        message = f"stream:{image_id}:{token_id}:{expires}".encode()
        digest = hmac.new(self._key(), message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _key(self) -> bytes:
        with self._key_lock:  # two first requests at once must not make two keys
            stored = self._settings.get(SIGNING_KEY_SETTING)
            if stored is None:
                stored = secrets.token_urlsafe(32)
                self._settings.set(SIGNING_KEY_SETTING, stored)

            return stored.encode()
