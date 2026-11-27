# Install guide

This guide sets up photobook-db on the computer where you use the photobook desktop app, step by step. The
server then runs in the background as a service of your user, never as root. For a server on a NAS or a
Raspberry Pi, see [Install](../README.md#install) in the README instead.

With the server on the same computer, the desktop app can also show a photo's or video's file in its folder
("Open Containing Folder").

The install script does all of this for you. It asks where things go, each with a default, and needs no
`sudo` once Podman is installed:

```sh
curl -fsSL https://github.com/OesusDevelopmentStudios/photobook-db/releases/latest/download/install.sh | sh
```

Add `-s -- --help` to see its options, e.g. `| sh -s -- --version 0.1.0` for a fixed release. To do it by
hand instead, follow the steps below.

## What you need

- **Podman 4.4 or newer**, with systemd. Fedora, Ubuntu 24.04 and Debian 13 have it.
- About 230 MB for the container image (with FFmpeg, for videos), and room for your photos and videos.

## 1. Install Podman

Fedora:
```sh
sudo dnf install podman
```

Ubuntu:
```sh
sudo apt install podman
```

Arch:
```sh
sudo pacman -S podman
```

## 2. Make the folders

The photos and videos go into a library folder; the database and the thumbnails go into `.photobook` inside it, so
one folder holds everything to back up:

```sh
mkdir -p ~/Pictures/photobook-library/.photobook ~/.config/containers/systemd
```

To keep them elsewhere, use your own paths here and in the next step.

## 3. Write the unit file

The unit file tells systemd how to run the server's container. Copy all of this into a terminal:

```sh
cat > ~/.config/containers/systemd/photobook-db.container <<'EOF'
[Unit]
Description=photobook-db photo and video library server

[Container]
Image=ghcr.io/oesusdevelopmentstudios/photobook-db:latest
AutoUpdate=registry
ContainerName=photobook-db
# Only this computer can connect. For other devices too: PublishPort=8737:8737
PublishPort=127.0.0.1:8737:8737

# The photos and videos, and the database with the thumbnails
Volume=%h/Pictures/photobook-library:/library:Z
Volume=%h/Pictures/photobook-library/.photobook:/data:Z

# Lets the desktop app see that the server runs on this computer, and where the library is
Volume=/etc/machine-id:/run/host/machine-id:ro
Environment=PHOTOBOOK_HOST_LIBRARY_DIR=%h/Pictures/photobook-library

# Files the server writes belong to you
UserNS=keep-id:uid=1000,gid=1000

HealthCmd=python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8737/api/v1/server/info', timeout=5)"
HealthInterval=1m

[Service]
Restart=on-failure
# The first start downloads the image
TimeoutStartSec=900

[Install]
WantedBy=default.target
EOF
```

`%h` is your home folder; systemd fills it in. Some changes you may want:

- **Another port**, if 8737 is taken: change the first number only, e.g. `PublishPort=127.0.0.1:9000:8737`.
  The app then connects to port 9000.
- **Other devices too**, e.g. the app on a laptop: `PublishPort=8737:8737`. If the computer has a firewall,
  open the port, e.g. on Fedora:
  `sudo firewall-cmd --add-port=8737/tcp --permanent && sudo firewall-cmd --reload`.
- **A folder to import from**, with photos and videos that are already on this computer:
  `Volume=%h/Pictures/inbox:/import:ro,Z`. Without `ro`, an import can remove the originals.

## 4. Start it

```sh
systemctl --user daemon-reload
systemctl --user start photobook-db
```

The first start downloads the image, which can take a few minutes. The server then starts again each time
you log in. To start it at boot, also before you log in:

```sh
loginctl enable-linger
```

## 5. Create the admin account

On its first start, the server writes a one-time **setup code** to its log:

```sh
podman logs photobook-db 2>&1 | grep "setup code"
```

Open <http://localhost:8737/admin/setup> in a browser, enter the code and create the admin account. Then,
at <http://localhost:8737/admin>, create accounts for the other people who use the library.

## 6. Connect the desktop app

In photobook, choose **Use a Local Server…** on the Welcome page, set the port if you changed it, connect,
and log in with your account. The app warns that the connection is not encrypted (plain HTTP): on your own
computer that is fine.

## Later

- **Updates:** to install new releases by themselves, once a day:
  `systemctl --user enable --now podman-auto-update.timer`.
- **Stop or start it:** `systemctl --user stop photobook-db`, `systemctl --user start photobook-db`.
- **Backups:** copy the library folder, with `.photobook` in it. Stop the server first, so that the copy of the database is complete.
- **Remove it:** `systemctl --user stop photobook-db`, remove the unit file, then
  `systemctl --user daemon-reload`. The library folder stays; delete it yourself if you want to.
