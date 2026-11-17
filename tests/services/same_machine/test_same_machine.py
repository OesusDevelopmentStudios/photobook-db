# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pathlib import Path

import pytest

from pictorio_db.services.same_machine import SameMachine, app_specific_id
from pictorio_db.utils.problems import Problem

MACHINE_ID = "0123456789abcdef0123456789abcdef"
# printf pictorio-db | openssl dgst -sha256 -hmac 0123456789abcdef0123456789abcdef: what the desktop app sends
CLIENT_ID = "9d623a361e613d33fd9937381c885a2ef999fa19c152bbbcadbf789bee807420"
LIBRARY = Path("/home/kid/Pictures/pictorio-library")


def same_machine(tmp_path: Path, machine_id_text: str | None) -> SameMachine:
    machine_id_file = tmp_path / "machine-id"
    if machine_id_text is not None:
        machine_id_file.write_text(machine_id_text)

    return SameMachine(machine_id_file, LIBRARY)


def test_the_id_is_an_hmac_of_the_machine_id_as_written_in_the_file() -> None:
    assert app_specific_id(MACHINE_ID) == CLIENT_ID
    assert app_specific_id(MACHINE_ID + "\n") == CLIENT_ID  # the file ends with a newline


def test_gives_the_library_folder_to_a_client_on_this_computer(tmp_path: Path) -> None:
    assert same_machine(tmp_path, MACHINE_ID + "\n").library_dir_for(CLIENT_ID) == LIBRARY
    assert same_machine(tmp_path, MACHINE_ID).library_dir_for(CLIENT_ID.upper()) == LIBRARY


@pytest.mark.parametrize(
    "client_id",
    [
        app_specific_id("fedcba9876543210fedcba9876543210"),  # another computer
        MACHINE_ID,  # the machine ID itself: it never leaves the computer
        "",
    ],
)
def test_refuses_any_other_client(tmp_path: Path, client_id: str) -> None:
    with pytest.raises(Problem) as refusal:
        same_machine(tmp_path, MACHINE_ID).library_dir_for(client_id)

    assert refusal.value.status == 404
    assert refusal.value.code == "not_same_machine"


# E.g. in a container without the host's machine ID mounted: no client is on the same computer.
@pytest.mark.parametrize("machine_id_text", [None, "", "  \n"])
def test_refuses_everyone_without_a_machine_id(tmp_path: Path, machine_id_text: str | None) -> None:
    with pytest.raises(Problem):
        same_machine(tmp_path, machine_id_text).library_dir_for(app_specific_id(""))
