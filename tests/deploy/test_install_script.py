# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""deploy/install.sh, run with sh against fake podman, systemctl, loginctl, curl and id, in a temporary home
folder: what it asks, the unit file it writes and what it starts. Nothing here touches the real system."""

import fcntl
import os
import pty
import subprocess
import termios
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "install.sh"
SETUP_CODE = "k7Qx2m"
# As the server writes it on its first start
SETUP_LOG_LINE = (
    f"No admin account exists yet. Create it at /admin/setup on this server with the setup code {SETUP_CODE}"
)

FAKE_COMMANDS = {
    "podman": f"""
case $1 in
    --version) echo "podman version ${{FAKE_PODMAN_VERSION:-5.6.0}}" ;;
    logs) echo "{SETUP_LOG_LINE}" ;;
esac
""",
    # is-failed: the service has not failed
    "systemctl": """
case $* in
    *is-failed*) exit 1 ;;
esac
""",
    "loginctl": "",
    "curl": "",  # the server answers at once
    "id": 'echo "${FAKE_UID:-1000}"',
}


@dataclass
class Machine:
    """A computer as the script sees it: a home folder, and the commands it calls."""

    home: Path
    environment: dict[str, str]
    calls_file: Path

    @property
    def unit_file(self) -> Path:
        return self.home / ".config" / "containers" / "systemd" / "photobook-db.container"

    def unit_lines(self) -> list[str]:
        return [line for line in self.unit_file.read_text().splitlines() if line and not line.startswith("#")]

    def calls(self) -> list[str]:
        return self.calls_file.read_text().splitlines() if self.calls_file.exists() else []


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    home = tmp_path / "home"
    home.mkdir()
    fakes = tmp_path / "bin"
    fakes.mkdir()
    calls_file = tmp_path / "calls"
    for name, body in FAKE_COMMANDS.items():
        fake = fakes / name
        fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "$FAKE_CALLS"\n{body}\n')
        fake.chmod(0o755)

    environment = {
        "PATH": f"{fakes}:/usr/bin:/bin",
        "HOME": str(home),
        "FAKE_CALLS": str(calls_file),
        "LANG": "C.UTF-8",
    }
    return Machine(home, environment, calls_file)


def install(machine: Machine, *options: str) -> subprocess.CompletedProcess[str]:
    """Without a terminal, as under a CI job: only options and --yes."""
    return subprocess.run(
        ["sh", str(SCRIPT), *options],
        env=machine.environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,  # no controlling terminal: /dev/tty cannot be opened
        timeout=30,
    )


def install_answering(machine: Machine, answers: list[str], *options: str) -> tuple[int, str]:
    """In a terminal of its own, as under `curl … | sh`, typing the answers in order. The exit code and what
    the terminal showed."""
    main, terminal = pty.openpty()
    process = subprocess.Popen(
        ["sh", str(SCRIPT), *options],
        env=machine.environment,
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        start_new_session=True,
        preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),  # the terminal becomes /dev/tty
    )
    os.close(terminal)
    os.write(main, "".join(answer + "\n" for answer in answers).encode())
    shown = b""
    while True:
        try:
            output = os.read(main, 4096)
        except OSError:  # the script ended and closed the terminal
            break
        if not output:
            break
        shown += output

    os.close(main)
    return process.wait(timeout=30), shown.decode(errors="replace")


def test_installs_with_the_defaults(machine: Machine) -> None:
    library = machine.home / "Pictures" / "photobook-library"

    result = install(machine, "--yes")

    assert result.returncode == 0, result.stderr
    lines = machine.unit_lines()
    assert "Image=ghcr.io/oesusdevelopmentstudios/photobook-db:latest" in lines
    assert "AutoUpdate=registry" in lines
    assert "PublishPort=127.0.0.1:8737:8737" in lines  # only this computer
    assert f"Volume={library}:/library:Z" in lines
    assert f"Volume={library}/.photobook:/data:Z" in lines
    assert not any(line.startswith("Volume=") and ":/import:" in line for line in lines)
    if Path("/etc/machine-id").exists():
        assert "Volume=/etc/machine-id:/run/host/machine-id:ro" in lines
        assert f"Environment=PHOTOBOOK_HOST_LIBRARY_DIR={library}" in lines
    assert (library / ".photobook").is_dir()
    calls = machine.calls()
    assert calls.index("systemctl --user daemon-reload") < calls.index("systemctl --user restart photobook-db")
    assert "loginctl enable-linger" in calls  # starts at boot
    assert f"http://localhost:8737/admin/setup with the setup code {SETUP_CODE}" in result.stdout


def test_takes_every_answer_as_an_option(machine: Machine, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    library = tmp_path / "library"

    result = install(
        machine, "--yes", "--library", str(library), "--database", str(tmp_path / "database"),
        "--import", str(inbox), "--port", "9000", "--lan", "--no-boot", "--version", "v0.1.0",
    )

    assert result.returncode == 0, result.stderr
    lines = machine.unit_lines()
    assert "Image=ghcr.io/oesusdevelopmentstudios/photobook-db:0.1.0" in lines
    assert "AutoUpdate=registry" not in lines  # a fixed release
    assert "PublishPort=9000:8737" in lines  # other devices too
    assert f"Volume={library}:/library:Z" in lines
    assert f"Volume={tmp_path / 'database'}:/data:Z" in lines
    assert f"Volume={inbox}:/import:ro,Z" in lines
    assert not any(call.startswith("loginctl") for call in machine.calls())
    assert "http://localhost:9000/admin/setup" in result.stdout


def test_asks_in_the_terminal(machine: Machine, tmp_path: Path) -> None:
    # Library typed with ~; Enter for the database (in the library), the import folder (none), the port and
    # the LAN (no); no start at boot.
    code, shown = install_answering(machine, ["~/Photos", "", "", "", "", "n"])

    assert code == 0, shown
    assert "Library folder, for the photos and videos" in shown
    lines = machine.unit_lines()
    assert f"Volume={machine.home}/Photos:/library:Z" in lines
    assert f"Volume={machine.home}/Photos/.photobook:/data:Z" in lines
    assert "PublishPort=127.0.0.1:8737:8737" in lines
    assert not any(call.startswith("loginctl") for call in machine.calls())


def test_needs_a_terminal_or_yes(machine: Machine) -> None:
    result = install(machine)

    assert result.returncode != 0
    assert "--yes" in result.stderr
    assert not machine.unit_file.exists()


def test_refuses_root(machine: Machine) -> None:
    machine.environment["FAKE_UID"] = "0"

    result = install(machine, "--yes")

    assert result.returncode != 0
    assert "not as root" in result.stderr
    assert not machine.unit_file.exists()


def test_needs_podman_4_4(machine: Machine) -> None:
    machine.environment["FAKE_PODMAN_VERSION"] = "4.3.1"

    result = install(machine, "--yes")

    assert result.returncode != 0
    assert "Podman 4.4" in result.stderr
    assert not machine.unit_file.exists()


@pytest.mark.parametrize("library", ["/tmp/my photos", "/tmp/a:b", "/tmp/100%"])
def test_refuses_a_folder_podman_cannot_mount(machine: Machine, library: str) -> None:
    result = install(machine, "--yes", "--library", library)

    assert result.returncode != 0
    assert "cannot contain" in result.stderr
    assert not machine.unit_file.exists()


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_refuses_a_wrong_port(machine: Machine, port: str) -> None:
    result = install(machine, "--yes", "--port", port)

    assert result.returncode != 0
    assert "not a port number" in result.stderr


def test_keeps_an_existing_unit_unless_asked_to_replace_it(machine: Machine) -> None:
    machine.unit_file.parent.mkdir(parents=True)
    machine.unit_file.write_text("[Container]\nImage=localhost/photobook-db:latest\n")

    kept = install(machine, "--yes")

    assert kept.returncode != 0
    assert "--replace" in kept.stderr
    assert "localhost/photobook-db" in machine.unit_file.read_text()

    replaced = install(machine, "--yes", "--replace")

    assert replaced.returncode == 0, replaced.stderr
    assert "Image=ghcr.io/oesusdevelopmentstudios/photobook-db:latest" in machine.unit_lines()
