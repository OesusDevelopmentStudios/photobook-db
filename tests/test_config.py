# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pathlib import Path

import pytest

from pictorio_db.config import DEFAULT_HOST, DEFAULT_PORT, Config, ConfigError, config_from_environment

HOME = {"HOME": "/home/user"}


def test_uses_defaults_without_environment_variables() -> None:
    assert config_from_environment(HOME) == Config(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        data_dir=Path("/home/user/.local/share/pictorio-db"),
        library_dir=Path("/home/user/Pictures/pictorio-library"),
    )


def test_reads_environment_variables() -> None:
    environment = HOME | {
        "PICTORIO_HOST": "0.0.0.0",
        "PICTORIO_PORT": "9000",
        "PICTORIO_DATA_DIR": "/data",
        "PICTORIO_LIBRARY_DIR": "/library",
        "PICTORIO_IMPORT_DIR": "/import",
    }

    assert config_from_environment(environment) == Config(
        host="0.0.0.0", port=9000, data_dir=Path("/data"), library_dir=Path("/library"), import_dir=Path("/import")
    )


@pytest.mark.parametrize("variable", ["PICTORIO_DATA_DIR", "PICTORIO_LIBRARY_DIR", "PICTORIO_IMPORT_DIR"])
def test_relative_folders_become_absolute_without_dot_dot(
    variable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "server").mkdir()
    monkeypatch.chdir(tmp_path / "server")

    config = config_from_environment(HOME | {variable: "../test/./library"})

    folder = {"PICTORIO_DATA_DIR": config.data_dir, "PICTORIO_LIBRARY_DIR": config.library_dir,
              "PICTORIO_IMPORT_DIR": config.import_dir}[variable]
    assert folder == tmp_path.resolve() / "test" / "library"


def test_data_folder_follows_xdg_data_home() -> None:
    environment = HOME | {"XDG_DATA_HOME": "/home/user/data"}

    assert config_from_environment(environment).data_dir == Path("/home/user/data/pictorio-db")


@pytest.mark.parametrize("variable", ["PICTORIO_DATA_DIR", "PICTORIO_LIBRARY_DIR", "PICTORIO_IMPORT_DIR"])
def test_rejects_empty_folders(variable: str) -> None:
    with pytest.raises(ConfigError, match=variable):
        config_from_environment(HOME | {variable: ""})


@pytest.mark.parametrize("port_text", ["", "http", "-1", "0", "65536", "80.5"])
def test_rejects_invalid_port(port_text: str) -> None:
    with pytest.raises(ConfigError, match="PICTORIO_PORT"):
        config_from_environment(HOME | {"PICTORIO_PORT": port_text})


@pytest.mark.parametrize(
    "text, trusted",
    [(None, ("127.0.0.1",)), ("*", ("*",)), (" 127.0.0.1, 10.88.0.0/16 ,::1", ("127.0.0.1", "10.88.0.0/16", "::1"))],
    ids=["unset", "any", "addresses and networks"],
)
def test_reads_the_trusted_proxies(text: str | None, trusted: tuple[str, ...]) -> None:
    environment = HOME if text is None else HOME | {"PICTORIO_TRUSTED_PROXIES": text}

    assert config_from_environment(environment).trusted_proxies == trusted


@pytest.mark.parametrize("text", ["", " , ", "localhost", "10.0.0.0/33"])
def test_rejects_invalid_trusted_proxies(text: str) -> None:
    with pytest.raises(ConfigError, match="PICTORIO_TRUSTED_PROXIES"):
        config_from_environment(HOME | {"PICTORIO_TRUSTED_PROXIES": text})
