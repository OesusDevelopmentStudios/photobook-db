# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Settings read from environment variables when the server starts."""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"  # only this computer; the container image sets 0.0.0.0
DEFAULT_PORT = 8737
DEFAULT_TRUSTED_PROXIES = ("127.0.0.1",)  # a reverse proxy on the same computer, outside a container
DEFAULT_HOST_MACHINE_ID_FILE = Path("/etc/machine-id")  # outside a container: this computer's own


class ConfigError(Exception):
    """An environment variable has a value the server cannot use."""


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path  # the database and the thumbnails; the container image sets /data
    library_dir: Path  # the images, arranged by their tags; the container image sets /library
    import_dir: Path | None = None  # folders admins can import from on the server; the container sets /import
    # Addresses whose X-Forwarded-For and X-Forwarded-Proto are believed, e.g. from Caddy: then the server knows
    # that the browser used HTTPS, and which address it came from. "*" trusts every address.
    trusted_proxies: tuple[str, ...] = DEFAULT_TRUSTED_PROXIES
    # The machine ID of the computer the server runs on, and the library folder as programs there see it: a
    # desktop app on that computer can then open an image's folder. In a container, both differ from the
    # container's own; the install script mounts the one and sets the other.
    host_machine_id_file: Path = DEFAULT_HOST_MACHINE_ID_FILE
    host_library_dir: Path | None = None  # None: library_dir


def config_from_environment(environment: Mapping[str, str] = os.environ) -> Config:
    return Config(
        host=environment.get("PICTORIO_HOST", DEFAULT_HOST),
        port=_parse_port(environment.get("PICTORIO_PORT", str(DEFAULT_PORT))),
        data_dir=_data_dir(environment),
        library_dir=_library_dir(environment),
        import_dir=_import_dir(environment),
        trusted_proxies=_trusted_proxies(environment.get("PICTORIO_TRUSTED_PROXIES")),
        host_machine_id_file=_host_path(environment, "PICTORIO_HOST_MACHINE_ID_FILE") or DEFAULT_HOST_MACHINE_ID_FILE,
        host_library_dir=_host_path(environment, "PICTORIO_HOST_LIBRARY_DIR"),
    )


def _trusted_proxies(text: str | None) -> tuple[str, ...]:
    """"*", or addresses and networks separated by commas: "127.0.0.1, 10.88.0.0/16"."""
    if text is None:
        return DEFAULT_TRUSTED_PROXIES

    entries = tuple(entry.strip() for entry in text.split(",") if entry.strip())
    if not entries:
        raise ConfigError("PICTORIO_TRUSTED_PROXIES must not be empty; leave it unset for 127.0.0.1")

    for entry in entries:
        if entry != "*":
            try:
                ipaddress.ip_network(entry)
            except ValueError:
                raise ConfigError(f"PICTORIO_TRUSTED_PROXIES: {entry!r} is not an address, a network or *") from None

    return entries


def _parse_port(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ConfigError(f"PICTORIO_PORT must be a number from 1 to 65535, not {text!r}")

    return int(text)


# Without PICTORIO_DATA_DIR: ~/.local/share/pictorio-db, or the same under XDG_DATA_HOME if it is set.
def _data_dir(environment: Mapping[str, str]) -> Path:
    if "PICTORIO_DATA_DIR" in environment:
        return _folder_from(environment, "PICTORIO_DATA_DIR")

    data_home = Path(environment.get("XDG_DATA_HOME") or _home(environment) / ".local" / "share")
    return data_home / "pictorio-db"


def _library_dir(environment: Mapping[str, str]) -> Path:
    if "PICTORIO_LIBRARY_DIR" in environment:
        return _folder_from(environment, "PICTORIO_LIBRARY_DIR")

    return _home(environment) / "Pictures" / "pictorio-library"


def _import_dir(environment: Mapping[str, str]) -> Path | None:
    """Unset: folder imports are off."""
    if "PICTORIO_IMPORT_DIR" in environment:
        return _folder_from(environment, "PICTORIO_IMPORT_DIR")

    return None


def _host_path(environment: Mapping[str, str], variable: str) -> Path | None:
    """A path on the computer the server runs on, taken as written: in a container it need not exist."""
    if variable not in environment:
        return None

    path = Path(environment[variable])
    if not path.is_absolute():
        raise ConfigError(f"{variable} must be an absolute path, not {environment[variable]!r}")

    return path


def _folder_from(environment: Mapping[str, str], variable: str) -> Path:
    if not environment[variable]:
        raise ConfigError(f"{variable} must not be empty")

    return Path(environment[variable]).expanduser().resolve()


def _home(environment: Mapping[str, str]) -> Path:
    return Path(environment.get("HOME", str(Path.home())))
