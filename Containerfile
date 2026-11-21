# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios
#
# Container image for photobook-db (amd64 and arm64). Build and run:
#
#   podman build -t photobook-db .
#   podman run -d --name photobook-db -p 8737:8737 \
#       -v photobook-data:/data -v ~/Pictures/photobook-library:/library:Z photobook-db

# The Debian release is named, so a rebuild does not switch the system underneath.
FROM docker.io/library/python:3.14-slim-trixie

# No .pyc files in the image, log lines appear at once, no pip cache or update checks
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/photobook-db
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# The server does not need root. UID 1000 is the first user account on most systems, so files it
# writes into mounted folders can belong to that user.
RUN groupadd --gid 1000 photobook \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin photobook \
    && mkdir /data /library /import \
    && chown photobook:photobook /data /library /import
USER photobook

# Listen on all interfaces inside the container; publish the port with -p 8737:8737.
# Mount volumes at /data and /library, or the database and the images are lost when the container
# is removed. Folders to import from can be mounted at /import. The host's /etc/machine-id, mounted at
# /run/host/machine-id, tells a desktop app on the same computer where the library is (see the README);
# without it the container's own machine ID never matches.
ENV PHOTOBOOK_HOST=0.0.0.0 \
    PHOTOBOOK_PORT=8737 \
    PHOTOBOOK_DATA_DIR=/data \
    PHOTOBOOK_LIBRARY_DIR=/library \
    PHOTOBOOK_IMPORT_DIR=/import \
    PHOTOBOOK_HOST_MACHINE_ID_FILE=/run/host/machine-id
EXPOSE 8737

CMD ["photobook-db", "serve"]
