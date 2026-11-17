# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Whether a client runs on the server's own computer, and then where the library folder is there, so the
desktop app can show an image's file in the file manager.

Both sides know the computer's machine ID (/etc/machine-id). The client sends an ID derived from it,
HMAC-SHA256 with the machine ID as written in the file as the key and "pictorio-db" as the message, in hex,
so the machine ID itself never leaves the computer. The client's address is not used: behind a reverse
proxy, or through rootless Podman's port forwarding, a remote client can look local and a local one remote."""

import hashlib
import hmac
from pathlib import Path

from pictorio_db.utils.problems import Problem

APP_SPECIFIC_MESSAGE = b"pictorio-db"


def app_specific_id(machine_id: str) -> str:
    """What a client on the computer with this machine ID sends."""
    return hmac.new(machine_id.strip().encode(), APP_SPECIFIC_MESSAGE, hashlib.sha256).hexdigest()


class SameMachine:
    def __init__(self, machine_id_file: Path, host_library_dir: Path) -> None:
        """machine_id_file: the machine ID of the computer the server runs on, e.g. mounted into a container.
        Read once; if it cannot be read, no client is on the same machine. host_library_dir: the library
        folder as that computer's programs see it."""
        self._expected_id = _expected_id(machine_id_file)
        self._host_library_dir = host_library_dir

    def library_dir_for(self, client_id: str) -> Path:
        """Raises 404 not_same_machine unless the client sent this computer's ID."""
        if self._expected_id is None or not hmac.compare_digest(self._expected_id, client_id.strip().lower()):
            raise Problem(
                404,
                "not_same_machine",
                "Not on the same computer",
                "The library folder is only given to a client on the computer the server runs on.",
            )

        return self._host_library_dir


def _expected_id(machine_id_file: Path) -> str | None:
    try:
        machine_id = machine_id_file.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None

    return app_specific_id(machine_id) if machine_id else None


__all__ = ["APP_SPECIFIC_MESSAGE", "SameMachine", "app_specific_id"]
