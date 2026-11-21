#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios
#
# Installs photobook-db on this computer: a Podman container that systemd runs as a service of your user,
# never as root. It asks where things go, each with a default, writes the Quadlet unit, starts the server
# and tells you how to create the admin account. docs/local-server.md does the same by hand.
#
#   curl -fsSL https://github.com/OesusDevelopmentStudios/photobook-db/releases/latest/download/install.sh | sh
#   curl -fsSL …/install.sh | sh -s -- --version 0.1.0     # a fixed release instead of the latest
#   sh install.sh --help                                   # every question as an option

set -eu

IMAGE=ghcr.io/oesusdevelopmentstudios/photobook-db
SERVER_PORT=8737  # inside the container
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
UNIT_FILE="$UNIT_DIR/photobook-db.container"
START_TIMEOUT=900  # seconds; the first start downloads the image

usage() {
    cat <<'EOF'
Installs the photobook-db server for your user with Podman and systemd.

Usage: install.sh [options]

  --library DIR    where the images go (default: ~/Pictures/photobook-library)
  --database DIR   the database and thumbnails (default: .photobook in the library folder)
  --import DIR     a folder admins can import from, read only (default: none)
  --port PORT      the port on this computer (default: 8737)
  --lan            other devices can connect too (default: only this computer)
  --no-lan
  --boot           start the server at boot, also before you log in (default)
  --no-boot        start it when you log in
  --version X      a fixed release, e.g. 0.1.0, not updated by itself (default: the latest, updated daily
                   if podman-auto-update.timer is on)
  --replace        replace an existing photobook-db unit
  --yes            ask nothing: use the options given and the defaults
  -h, --help       this text
EOF
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# The answer to a question, read from the terminal: stdin is this script itself under `curl … | sh`.
# Prints the default if nothing is typed, or if --yes was given.
ask() {
    if [ "$assume_defaults" = yes ]; then
        printf '%s' "$2"
        return
    fi

    printf '%s [%s]: ' "$1" "$2" >/dev/tty
    IFS= read -r reply </dev/tty || fail "no answer"
    printf '%s' "${reply:-$2}"
}

ask_yes_no() {
    if [ "$2" = yes ]; then hint=Y/n; else hint=y/N; fi
    while :; do
        answer=$(ask "$1" "$hint") || exit 1
        case $answer in
            Y/n | y/N) printf '%s' "$2"; return ;;
            y | Y | yes) printf yes; return ;;
            n | N | no) printf no; return ;;
        esac
    done
}

# An absolute path; ~ is the home folder. Podman's volume syntax and systemd's specifiers rule out some
# characters.
folder() {
    # shellcheck disable=SC2088  # a ~ typed as an answer, which the shell does not expand
    case $1 in
        "~") path=$HOME ;;
        "~/"*) path="$HOME/${1#"~/"}" ;;
        /*) path=$1 ;;
        *) path="$PWD/$1" ;;
    esac
    case $path in
        *[[:space:]:%]*) fail "$path: a folder for the server cannot contain spaces, ':' or '%'" ;;
    esac
    printf '%s' "${path%/}"
}

version=""
library=""
database=""
import_dir=""
import_given=no
port=""
lan=""
boot=""
assume_defaults=no
replace=no

while [ $# -gt 0 ]; do
    case $1 in
        --library | --database | --import | --port | --version)
            [ $# -ge 2 ] || fail "$1 needs a value"
            case $1 in
                --library) library=$2 ;;
                --database) database=$2 ;;
                --import) import_dir=$2; import_given=yes ;;
                --port) port=$2 ;;
                --version) version=${2#v} ;;
            esac
            shift
            ;;
        --lan) lan=yes ;;
        --no-lan) lan=no ;;
        --boot) boot=yes ;;
        --no-boot) boot=no ;;
        --replace) replace=yes ;;
        --yes) assume_defaults=yes ;;
        -h | --help) usage; exit 0 ;;
        *) fail "unknown option $1 (see --help)" ;;
    esac
    shift
done

# What is needed before anything is asked
[ "$(id -u)" -ne 0 ] || fail "run this as your own user, not as root: the server runs as a service of yours"
command -v podman >/dev/null 2>&1 \
    || fail "Podman is needed: sudo dnf install podman (Ubuntu, Debian: sudo apt install podman)"
podman_version=$(podman --version | sed -n 's/^podman version \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')
# shellcheck disable=SC2086  # the two numbers
set -- $podman_version
if [ $# -ne 2 ] || [ "$1" -lt 4 ] || { [ "$1" -eq 4 ] && [ "$2" -lt 4 ]; }; then
    fail "Podman 4.4 or newer is needed; this is $(podman --version)"
fi
systemctl --user show-environment >/dev/null 2>&1 \
    || fail "systemd does not run for your user; log in to a desktop first"
# Not ':', a special builtin: a failed redirection on one ends the whole script.
if [ "$assume_defaults" = no ] && ! { true </dev/tty; } 2>/dev/null; then
    fail "no terminal to ask in: give the options and --yes (see --help)"
fi

if [ -e "$UNIT_FILE" ] && [ "$replace" = no ]; then
    [ "$assume_defaults" = no ] || fail "$UNIT_FILE exists already; add --replace to replace it"
    [ "$(ask_yes_no "photobook-db is set up already. Replace its unit file? The images and database stay" no)" = yes ] \
        || { echo "Nothing changed."; exit 0; }
fi

# The questions not answered by options
[ -n "$library" ] || library=$(ask "Library folder, for the images" "$HOME/Pictures/photobook-library")
library=$(folder "$library")
[ -n "$database" ] || database=$(ask "Database folder, also for the thumbnails" "$library/.photobook")
database=$(folder "$database")
[ "$import_given" = yes ] || import_dir=$(ask "Folder to import from, read only (empty: none)" "")
[ -z "$import_dir" ] || import_dir=$(folder "$import_dir")
[ -n "$port" ] || port=$(ask "Port" 8737)
case $port in
    "" | *[!0-9]*) fail "$port is not a port number" ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    fail "$port is not a port number"
fi
[ -n "$lan" ] || lan=$(ask_yes_no "Can other devices in the network connect too?" no)
[ -n "$boot" ] || boot=$(ask_yes_no "Start the server at boot, also before you log in?" yes)

[ -z "$import_dir" ] || [ -d "$import_dir" ] || fail "$import_dir does not exist"
mkdir -p "$library" "$database" "$UNIT_DIR"

if [ -n "$version" ]; then
    image_lines="Image=$IMAGE:$version"
else
    image_lines="Image=$IMAGE:latest
AutoUpdate=registry"
fi

if [ "$lan" = yes ]; then
    publish="PublishPort=$port:$SERVER_PORT"
else
    publish="PublishPort=127.0.0.1:$port:$SERVER_PORT"
fi

import_lines=""
[ -z "$import_dir" ] || import_lines="Volume=$import_dir:/import:ro,Z"

# Lets the desktop app see that the server runs on this computer; some containers have no machine ID.
machine_lines=""
if [ -r /etc/machine-id ]; then
    machine_lines="Volume=/etc/machine-id:/run/host/machine-id:ro
Environment=PHOTOBOOK_HOST_LIBRARY_DIR=$library"
fi

cat >"$UNIT_FILE" <<EOF
# Written by photobook-db's install.sh. Run it again to change the answers.

[Unit]
Description=photobook-db image library server

[Container]
$image_lines
ContainerName=photobook-db
$publish
Volume=$library:/library:Z
Volume=$database:/data:Z
$import_lines
$machine_lines
# Files the server writes belong to you
UserNS=keep-id:uid=1000,gid=1000
HealthCmd=python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$SERVER_PORT/api/v1/server/info', timeout=5)"
HealthInterval=1m

[Service]
Restart=on-failure
TimeoutStartSec=$START_TIMEOUT

[Install]
WantedBy=default.target
EOF
echo "Wrote $UNIT_FILE"

systemctl --user daemon-reload
echo "Starting photobook-db; the first start downloads the image, which can take a few minutes…"
systemctl --user restart photobook-db
[ "$boot" = no ] || loginctl enable-linger

if command -v curl >/dev/null 2>&1; then
    waited=0
    until curl -fsS -o /dev/null "http://127.0.0.1:$port/api/v1/server/info" 2>/dev/null; do
        systemctl --user is-failed --quiet photobook-db \
            && fail "the server did not start; see: journalctl --user -u photobook-db"
        [ "$waited" -lt "$START_TIMEOUT" ] || fail "the server did not answer; see: journalctl --user -u photobook-db"
        sleep 2
        waited=$((waited + 2))
    done
fi

echo
echo "photobook-db runs at http://localhost:$port"
setup_code=$(podman logs photobook-db 2>&1 | sed -n 's/.*setup code \([^[:space:]]*\).*/\1/p' | tail -n 1)
if [ -n "$setup_code" ]; then
    echo "Create the admin account at http://localhost:$port/admin/setup with the setup code $setup_code"
else
    echo "Log in to the web admin at http://localhost:$port/admin"
fi
echo "Then connect the desktop app to http://localhost:$port"
if [ "$lan" = yes ]; then
    echo "Other devices connect to this computer's name or address, port $port;"
    echo "open that port in the firewall if there is one."
fi
