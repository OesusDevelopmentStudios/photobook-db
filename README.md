# photobook-db

[![CI](https://github.com/OesusDevelopmentStudios/photobook-db/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/OesusDevelopmentStudios/photobook-db/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/tag/OesusDevelopmentStudios/photobook-db?sort=semver&label=release)](https://github.com/OesusDevelopmentStudios/photobook-db/tags)
[![License: GPL-3.0](https://img.shields.io/github/license/OesusDevelopmentStudios/photobook-db)](LICENSE)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FOesusDevelopmentStudios%2Fphotobook-db%2Fmaster%2Fpyproject.toml)](pyproject.toml)
[![Container image](https://img.shields.io/badge/container-ghcr.io-blue?logo=podman)](https://github.com/OesusDevelopmentStudios/photobook-db/pkgs/container/photobook-db)

Image library server for [Photobook](https://github.com/OesusDevelopmentStudios/photobook).

photobook-db stores your images in a library folder, arranges them by their tags and keeps a database
of tags and metadata. The Photobook desktop app connects to it to search, browse, tag and upload
images. Web pages on the server (`/admin`) are used to configure it.

It runs on a desktop PC, a NAS or a Raspberry Pi, on 64-bit Linux (amd64 or arm64).

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
mkdir -p ~/Pictures/photobook-library ~/.config/containers/systemd
curl -o ~/.config/containers/systemd/photobook-db.container \
    https://raw.githubusercontent.com/OesusDevelopmentStudios/photobook-db/master/deploy/photobook-db.container
systemctl --user daemon-reload
systemctl --user start photobook-db
loginctl enable-linger $USER     # start it at boot, without logging in
```

The first start downloads the image, which can take a few minutes on a Raspberry Pi. The images are kept in
`~/Pictures/photobook-library` and the database in the Podman volume `photobook-data`; both stay when
the container is updated. To keep the images elsewhere, or to import folders that are already on the
server, edit the `Volume=` lines in the unit file before starting it; the comments there explain them.

To install new releases by themselves, once a day:

```sh
systemctl --user enable --now podman-auto-update.timer
```

Then continue with [the first start](#first-start).

### With Docker

```sh
sudo mkdir -p /srv/photobook-library
sudo chown 1000:1000 /srv/photobook-library   # the server in the container runs as UID 1000
docker run -d --name photobook-db --restart unless-stopped -p 8737:8737 \
    -v photobook-data:/data -v /srv/photobook-library:/library \
    ghcr.io/oesusdevelopmentstudios/photobook-db:latest
```

To import folders that are already on the server, add e.g. `-v /srv/photos-inbox:/import:ro`. Without
`ro`, an import can remove the originals.

To update: `docker pull ghcr.io/oesusdevelopmentstudios/photobook-db:latest`, then remove the container
(`docker rm -f photobook-db`) and start it again with the same `docker run` command. The database and
the images are in the volume and the folder, so they stay.

Then continue with [the first start](#first-start).

### With pipx, without a container

```sh
sudo apt install pipx git        # Fedora: sudo dnf install pipx git
pipx install git+https://github.com/OesusDevelopmentStudios/photobook-db@v0.1.0
```

Replace `v0.1.0` with the [latest release](https://github.com/OesusDevelopmentStudios/photobook-db/tags).
To run it as a service that starts at boot:

```sh
mkdir -p ~/.config/systemd/user
curl -o ~/.config/systemd/user/photobook-db.service \
    https://raw.githubusercontent.com/OesusDevelopmentStudios/photobook-db/master/deploy/photobook-db.service
systemctl --user daemon-reload
systemctl --user enable --now photobook-db
loginctl enable-linger $USER
```

The database is in `~/.local/share/photobook-db`, the images in `~/Pictures/photobook-library`. The
[environment variables](#settings-for-the-server) in the unit file change that. To update, install the
new release over the old one: `pipx install --force git+https://…@v0.2.0`, then
`systemctl --user restart photobook-db`.

### First start

On its first start, the server writes a one-time **setup code** to its log:

```sh
podman logs photobook-db 2>&1 | grep "setup code"           # Podman
docker logs photobook-db 2>&1 | grep "setup code"           # Docker
journalctl --user -u photobook-db | grep "setup code"       # pipx
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
and what to change in photobook-db for it.

### Backups

Back up two things:

- **The images:** the library folder, e.g. `~/Pictures/photobook-library`.
- **The database:** the `photobook-data` volume (container), or `~/.local/share/photobook-db` (pipx). It
  also holds the thumbnails.

Stop the server while you copy the database, so that the copy is complete:

```sh
systemctl --user stop photobook-db
podman volume export photobook-data > photobook-data-backup.tar     # Docker: copy the volume's folder
systemctl --user start photobook-db
```

To restore it: stop the server, `podman volume import photobook-data photobook-data-backup.tar`, start it.

### Settings for the server

These environment variables are read when the server starts. The container image sets the folders to
`/data`, `/library` and `/import`; mount your folders there instead of changing them.

| Environment variable | Default | Meaning |
|---|---|---|
| `PHOTOBOOK_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` makes the server reachable from other computers |
| `PHOTOBOOK_PORT` | `8737` | Port to listen on |
| `PHOTOBOOK_DATA_DIR` | `~/.local/share/photobook-db` | Folder for the database. Created if missing |
| `PHOTOBOOK_LIBRARY_DIR` | `~/Pictures/photobook-library` | Folder for the images, arranged by their tags. Created if missing |
| `PHOTOBOOK_IMPORT_DIR` | not set | Folder that admins can import from with an import job, e.g. a NAS share. Unset: no folder imports |
| `PHOTOBOOK_TRUSTED_PROXIES` | `127.0.0.1` | Addresses (or networks, comma-separated, or `*`) of a reverse proxy whose `X-Forwarded-*` headers are believed. See [deploy/Caddyfile.example](deploy/Caddyfile.example) |
| `PHOTOBOOK_HOST_MACHINE_ID_FILE` | `/etc/machine-id` | The machine ID of the computer the server runs on. A desktop app that proves it runs there gets the library folder's path, to open an image's folder in the file manager. The container image reads `/run/host/machine-id`: mount the host's `/etc/machine-id` there, read-only, as [deploy/photobook-db.container](deploy/photobook-db.container) does |
| `PHOTOBOOK_HOST_LIBRARY_DIR` | `PHOTOBOOK_LIBRARY_DIR` | The library folder as programs on that computer see it; in a container, the host folder mounted at `/library` |

Everything else (path template, users, namespaces, tags, accent colour…) is set on the admin pages.

## Development

Needs Python 3.12 or newer. How to work on the code (its layout, the style, the checks before a commit, database and API changes) is in [CONTRIBUTING.md](CONTRIBUTING.md).

Set up a virtual environment in the repository and install the project with its development tools:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run the server (in the virtual environment):

```sh
photobook-db serve
curl http://127.0.0.1:8737/api/v1/server/info
```

The interactive API documentation is at <http://127.0.0.1:8737/api/docs>.

During development, point `PHOTOBOOK_DATA_DIR` and `PHOTOBOOK_LIBRARY_DIR` at scratch folders to keep
test data out of your home folder, e.g.
`PHOTOBOOK_DATA_DIR=/tmp/photobook-dev/data PHOTOBOOK_LIBRARY_DIR=/tmp/photobook-dev/library photobook-db serve`.
The tests always use temporary folders of their own.

### Building the image yourself

The [Containerfile](Containerfile) builds the image for amd64 or arm64:

```sh
podman build -t photobook-db .
```

To use it with the Quadlet unit, set `Image=localhost/photobook-db:latest` there and remove
`AutoUpdate=registry`.

On an amd64 PC, `podman build --platform linux/arm64 -t photobook-db:arm64 .` builds the arm64 image;
it needs QEMU's user-mode emulation (Fedora: `qemu-user-static-aarch64`). That build replaces the
local copy of the Python base image with its arm64 variant, so build the normal image with
`podman build --platform linux/amd64 -t photobook-db .` afterwards. The image also works with Docker.

### Releases

[CI](.github/workflows/ci.yml) runs the tests on every push. To publish a release, set the version in
`pyproject.toml`, commit, and push a tag for it:

```sh
git tag v0.1.0 && git push origin v0.1.0
```

[The release workflow](.github/workflows/release.yml) then tests again and publishes the image for
amd64 and arm64 as `ghcr.io/oesusdevelopmentstudios/photobook-db`. A new package on GitHub is private:
make it public once in its package settings, so that servers can download it without logging in.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
