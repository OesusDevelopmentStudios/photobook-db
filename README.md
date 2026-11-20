# pictorio-db

[![CI](https://github.com/OesusDevelopmentStudios/pictorio-db/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/OesusDevelopmentStudios/pictorio-db/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/tag/OesusDevelopmentStudios/pictorio-db?sort=semver&label=release)](https://github.com/OesusDevelopmentStudios/pictorio-db/tags)
[![License: GPL-3.0](https://img.shields.io/github/license/OesusDevelopmentStudios/pictorio-db)](LICENSE)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FOesusDevelopmentStudios%2Fpictorio-db%2Fmaster%2Fpyproject.toml)](pyproject.toml)
[![Container image](https://img.shields.io/badge/container-ghcr.io-blue?logo=podman)](https://github.com/OesusDevelopmentStudios/pictorio-db/pkgs/container/pictorio-db)

Image library server for [Pictorio](https://github.com/OesusDevelopmentStudios/pictorio).

pictorio-db stores your images in a library folder, arranges them by their tags and keeps a database
of tags and metadata. The Pictorio desktop app connects to it to search, browse, tag and upload
images. Web pages on the server (`/admin`) are used to configure it.

It runs on a desktop PC, a NAS or a Raspberry Pi, on 64-bit Linux (amd64 or arm64).

> **Status:** version 0.1. The server and its admin pages work; the desktop app learns to connect to
> it in a later version.

## Install

Pick one way:

| | Needs | Good for |
|---|---|---|
| [Podman and systemd](#with-podman-and-systemd) (recommended) | Podman 4.4 or newer | A Raspberry Pi or a PC; updates itself |
| [Docker](#with-docker) | Docker | A NAS that offers Docker, or Debian 12 |
| [pipx, without a container](#with-pipx-without-a-container) | Python 3.12 or newer | Trying it out on your own PC |

Podman 4.4+ and Python 3.12+ come with Debian 13 and the Raspberry Pi OS based on it, Ubuntu 24.04 and
Fedora. Debian 12 has older versions: use Docker there.

For a server on the computer where you use the desktop app, follow [the local server guide](docs/local-server.md)
instead: it sets the server up for that computer, so that the app can also open an image's folder.

The server uses port **8737**. If the computer has a firewall, open it, e.g. on Fedora:
`sudo firewall-cmd --add-port=8737/tcp --permanent && sudo firewall-cmd --reload`.

### With Podman and systemd

As your normal user, not root:

```sh
sudo apt install podman          # Fedora: sudo dnf install podman
mkdir -p ~/Pictures/pictorio-library ~/.config/containers/systemd
curl -o ~/.config/containers/systemd/pictorio-db.container \
    https://raw.githubusercontent.com/OesusDevelopmentStudios/pictorio-db/master/deploy/pictorio-db.container
systemctl --user daemon-reload
systemctl --user start pictorio-db
loginctl enable-linger $USER     # start it at boot, without logging in
```

The first start downloads the image, which can take a few minutes on a Raspberry Pi. The images are kept in
`~/Pictures/pictorio-library` and the database in the Podman volume `pictorio-data`; both stay when
the container is updated. To keep the images elsewhere, or to import folders that are already on the
server, edit the `Volume=` lines in the unit file before starting it; the comments there explain them.

To install new releases by themselves, once a day:

```sh
systemctl --user enable --now podman-auto-update.timer
```

Then continue with [the first start](#first-start).

### With Docker

```sh
sudo mkdir -p /srv/pictorio-library
sudo chown 1000:1000 /srv/pictorio-library   # the server in the container runs as UID 1000
docker run -d --name pictorio-db --restart unless-stopped -p 8737:8737 \
    -v pictorio-data:/data -v /srv/pictorio-library:/library \
    ghcr.io/oesusdevelopmentstudios/pictorio-db:latest
```

To import folders that are already on the server, add e.g. `-v /srv/photos-inbox:/import:ro`. Without
`ro`, an import can remove the originals.

To update: `docker pull ghcr.io/oesusdevelopmentstudios/pictorio-db:latest`, then remove the container
(`docker rm -f pictorio-db`) and start it again with the same `docker run` command. The database and
the images are in the volume and the folder, so they stay.

Then continue with [the first start](#first-start).

### With pipx, without a container

```sh
sudo apt install pipx git        # Fedora: sudo dnf install pipx git
pipx install git+https://github.com/OesusDevelopmentStudios/pictorio-db@v0.1.0
```

Replace `v0.1.0` with the [latest release](https://github.com/OesusDevelopmentStudios/pictorio-db/tags).
To run it as a service that starts at boot:

```sh
mkdir -p ~/.config/systemd/user
curl -o ~/.config/systemd/user/pictorio-db.service \
    https://raw.githubusercontent.com/OesusDevelopmentStudios/pictorio-db/master/deploy/pictorio-db.service
systemctl --user daemon-reload
systemctl --user enable --now pictorio-db
loginctl enable-linger $USER
```

The database is in `~/.local/share/pictorio-db`, the images in `~/Pictures/pictorio-library`. The
[environment variables](#settings-for-the-server) in the unit file change that. To update, install the
new release over the old one: `pipx install --force git+https://…@v0.2.0`, then
`systemctl --user restart pictorio-db`.

### First start

On its first start, the server writes a one-time **setup code** to its log:

```sh
podman logs pictorio-db 2>&1 | grep "setup code"           # Podman
docker logs pictorio-db 2>&1 | grep "setup code"           # Docker
journalctl --user -u pictorio-db | grep "setup code"       # pipx
```

Open `http://<server>:8737/admin/setup` in a browser, e.g. `http://raspberrypi.local:8737/admin/setup`,
enter the code and create the admin account. Then log in at `/admin`. There you:

- create accounts for the other people, and choose tags that some of them may not see;
- set where the images go in the library folder (the path template), the upload limit and the ratings;
- import folders that are already on the server, if you set up an import folder.

There is one admin account; it can be renamed and given a new password, but not removed.

### HTTPS

The server speaks plain HTTP. In your home network that is often enough. To reach it from the
internet, or to keep passwords off the network, put [Caddy](https://caddyserver.com) in front of it:
it gets HTTPS certificates by itself. [deploy/Caddyfile.example](deploy/Caddyfile.example) shows how,
and what to change in pictorio-db for it.

### Backups

Back up two things:

- **The images:** the library folder, e.g. `~/Pictures/pictorio-library`.
- **The database:** the `pictorio-data` volume (container), or `~/.local/share/pictorio-db` (pipx). It
  also holds the thumbnails.

Stop the server while you copy the database, so that the copy is complete:

```sh
systemctl --user stop pictorio-db
podman volume export pictorio-data > pictorio-data-backup.tar     # Docker: copy the volume's folder
systemctl --user start pictorio-db
```

To restore it: stop the server, `podman volume import pictorio-data pictorio-data-backup.tar`, start it.

### Settings for the server

These environment variables are read when the server starts. The container image sets the folders to
`/data`, `/library` and `/import`; mount your folders there instead of changing them.

| Environment variable | Default | Meaning |
|---|---|---|
| `PICTORIO_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` makes the server reachable from other computers |
| `PICTORIO_PORT` | `8737` | Port to listen on |
| `PICTORIO_DATA_DIR` | `~/.local/share/pictorio-db` | Folder for the database. Created if missing |
| `PICTORIO_LIBRARY_DIR` | `~/Pictures/pictorio-library` | Folder for the images, arranged by their tags. Created if missing |
| `PICTORIO_IMPORT_DIR` | not set | Folder that admins can import from with an import job, e.g. a NAS share. Unset: no folder imports |
| `PICTORIO_TRUSTED_PROXIES` | `127.0.0.1` | Addresses (or networks, comma-separated, or `*`) of a reverse proxy whose `X-Forwarded-*` headers are believed. See [deploy/Caddyfile.example](deploy/Caddyfile.example) |
| `PICTORIO_HOST_MACHINE_ID_FILE` | `/etc/machine-id` | The machine ID of the computer the server runs on. A desktop app that proves it runs there gets the library folder's path, to open an image's folder in the file manager. The container image reads `/run/host/machine-id`: mount the host's `/etc/machine-id` there, read-only, as [deploy/pictorio-db.container](deploy/pictorio-db.container) does |
| `PICTORIO_HOST_LIBRARY_DIR` | `PICTORIO_LIBRARY_DIR` | The library folder as programs on that computer see it; in a container, the host folder mounted at `/library` |

Everything else (path template, users, namespaces, tags, accent colour…) is set on the admin pages.

## Development

Needs Python 3.12 or newer.

Set up a virtual environment in the repository and install the project with its development tools:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run the tests and the style check:

```sh
pytest
flake8
```

Run the server (in the virtual environment):

```sh
pictorio-db serve
curl http://127.0.0.1:8737/api/v1/server/info
```

The interactive API documentation is at <http://127.0.0.1:8737/api/docs>.

During development, point `PICTORIO_DATA_DIR` and `PICTORIO_LIBRARY_DIR` at scratch folders to keep
test data out of your home folder, e.g.
`PICTORIO_DATA_DIR=/tmp/pictorio-dev/data PICTORIO_LIBRARY_DIR=/tmp/pictorio-dev/library pictorio-db serve`.
The tests always use temporary folders of their own.

The API schema is committed as [docs/openapi.json](docs/openapi.json), and a test checks that it
matches the code. After an intended API change, update it with:

```sh
pictorio-db openapi > docs/openapi.json
```

### Building the image yourself

The [Containerfile](Containerfile) builds the image for amd64 or arm64:

```sh
podman build -t pictorio-db .
```

To use it with the Quadlet unit, set `Image=localhost/pictorio-db:latest` there and remove
`AutoUpdate=registry`.

On an amd64 PC, `podman build --platform linux/arm64 -t pictorio-db:arm64 .` builds the arm64 image;
it needs QEMU's user-mode emulation (Fedora: `qemu-user-static-aarch64`). That build replaces the
local copy of the Python base image with its arm64 variant, so build the normal image with
`podman build --platform linux/amd64 -t pictorio-db .` afterwards. The image also works with Docker.

### Releases

[CI](.github/workflows/ci.yml) runs the tests on every push. To publish a release, set the version in
`pyproject.toml`, commit, and push a tag for it:

```sh
git tag v0.1.0 && git push origin v0.1.0
```

[The release workflow](.github/workflows/release.yml) then tests again and publishes the image for
amd64 and arm64 as `ghcr.io/oesusdevelopmentstudios/pictorio-db`. A new package on GitHub is private:
make it public once in its package settings, so that servers can download it without logging in.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
