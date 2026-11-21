# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pathlib import Path

import pytest

from photobook_db.config import DEFAULT_HOST, DEFAULT_PORT, Config, ConfigError, config_from_environment

HOME = {"HOME": "/home/user"}


def test_uses_defaults_without_environment_variables() -> None:
    assert config_from_environment(HOME) == Config(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        data_dir=Path("/home/user/.local/share/photobook-db"),
        library_dir=Path("/home/user/Pictures/photobook-library"),
    )


def test_reads_environment_variables() -> None:
    environment = HOME | {
        "PHOTOBOOK_HOST": "0.0.0.0",
        "PHOTOBOOK_PORT": "9000",
        "PHOTOBOOK_DATA_DIR": "/data",
        "PHOTOBOOK_LIBRARY_DIR": "/library",
        "PHOTOBOOK_IMPORT_DIR": "/import",
    }

    assert config_from_environment(environment) == Config(
        host="0.0.0.0", port=9000, data_dir=Path("/data"), library_dir=Path("/library"), import_dir=Path("/import")
    )


@pytest.mark.parametrize("variable", ["PHOTOBOOK_DATA_DIR", "PHOTOBOOK_LIBRARY_DIR", "PHOTOBOOK_IMPORT_DIR"])
def test_relative_folders_become_absolute_without_dot_dot(
    variable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "server").mkdir()
    monkeypatch.chdir(tmp_path / "server")

    config = config_from_environment(HOME | {variable: "../test/./library"})

    folder = {"PHOTOBOOK_DATA_DIR": config.data_dir, "PHOTOBOOK_LIBRARY_DIR": config.library_dir,
              "PHOTOBOOK_IMPORT_DIR": config.import_dir}[variable]
    assert folder == tmp_path.resolve() / "test" / "library"


def test_data_folder_follows_xdg_data_home() -> None:
    environment = HOME | {"XDG_DATA_HOME": "/home/user/data"}

    assert config_from_environment(environment).data_dir == Path("/home/user/data/photobook-db")


@pytest.mark.parametrize("variable", ["PHOTOBOOK_DATA_DIR", "PHOTOBOOK_LIBRARY_DIR", "PHOTOBOOK_IMPORT_DIR"])
def test_rejects_empty_folders(variable: str) -> None:
    with pytest.raises(ConfigError, match=variable):
        config_from_environment(HOME | {variable: ""})


@pytest.mark.parametrize("port_text", ["", "http", "-1", "0", "65536", "80.5"])
def test_rejects_invalid_port(port_text: str) -> None:
    with pytest.raises(ConfigError, match="PHOTOBOOK_PORT"):
        config_from_environment(HOME | {"PHOTOBOOK_PORT": port_text})


@pytest.mark.parametrize(
    "text, trusted",
    [(None, ("127.0.0.1",)), ("*", ("*",)), (" 127.0.0.1, 10.88.0.0/16 ,::1", ("127.0.0.1", "10.88.0.0/16", "::1"))],
    ids=["unset", "any", "addresses and networks"],
)
def test_reads_the_trusted_proxies(text: str | None, trusted: tuple[str, ...]) -> None:
    environment = HOME if text is None else HOME | {"PHOTOBOOK_TRUSTED_PROXIES": text}

    assert config_from_environment(environment).trusted_proxies == trusted


@pytest.mark.parametrize("text", ["", " , ", "localhost", "10.0.0.0/33"])
def test_rejects_invalid_trusted_proxies(text: str) -> None:
    with pytest.raises(ConfigError, match="PHOTOBOOK_TRUSTED_PROXIES"):
        config_from_environment(HOME | {"PHOTOBOOK_TRUSTED_PROXIES": text})


def test_reads_where_the_host_keeps_its_machine_id_and_the_library() -> None:
    environment = HOME | {
        "PHOTOBOOK_LIBRARY_DIR": "/library",
        "PHOTOBOOK_HOST_MACHINE_ID_FILE": "/run/host/machine-id",
        "PHOTOBOOK_HOST_LIBRARY_DIR": "/home/kid/Pictures/photobook-library",
    }

    config = config_from_environment(environment)

    assert config.host_machine_id_file == Path("/run/host/machine-id")
    assert config.host_library_dir == Path("/home/kid/Pictures/photobook-library")  # need not exist in a container


def test_uses_this_computers_machine_id_and_library_by_default() -> None:
    config = config_from_environment(HOME)

    assert config.host_machine_id_file == Path("/etc/machine-id")
    assert config.host_library_dir is None


@pytest.mark.parametrize("variable", ["PHOTOBOOK_HOST_MACHINE_ID_FILE", "PHOTOBOOK_HOST_LIBRARY_DIR"])
def test_host_paths_must_be_absolute(variable: str) -> None:
    with pytest.raises(ConfigError, match=variable):
        config_from_environment(HOME | {variable: "Pictures/photobook-library"})
