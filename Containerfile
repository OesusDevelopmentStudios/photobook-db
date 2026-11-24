# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios
#
# Container image for photobook-db (amd64 and arm64). Build and run:
#
#   podman build -t photobook-db .
#   podman run -d --name photobook-db -p 8737:8737 \
#       -v photobook-data:/data -v ~/Pictures/photobook-library:/library:Z photobook-db

# --- FFmpeg, built with only what the server needs: reading the video containers it takes, decoding their
# pictures, and writing single frames as PNG. Debian's ffmpeg package would add about 450 MB of GPU and speech
# libraries the server never uses.
FROM docker.io/library/python:3.14-slim-trixie AS ffmpeg

ARG FFMPEG_VERSION=8.1.3
# The release tarball's SHA-256, taken once its signature was checked with the FFmpeg release key
# FCF986EA15E6E293A5644F10B4322F04D67658D8 (ffmpeg.org publishes signatures, not checksums).
ARG FFMPEG_SHA256=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential ca-certificates curl libdav1d-dev nasm pkg-config xz-utils zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl -fsSL -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum --check - \
    && tar xf ffmpeg.tar.xz --strip-components=1

# Video: the codecs of cameras and phones, AV1 through dav1d. Audio decoders only so a file's sound is read
# without complaint. Filters: scaling, turning upright, and picking frames for scrub previews.
RUN ./configure --prefix=/opt/ffmpeg \
        --disable-everything --disable-autodetect --disable-doc --disable-network \
        --disable-ffplay --disable-avdevice \
        --enable-zlib --enable-libdav1d \
        --enable-protocol=file,pipe \
        --enable-demuxer=mov,matroska,avi \
        --enable-parser=h264,hevc,vp8,vp9,av1,mpeg4video,mjpeg,aac,opus,vorbis,mpegaudio,ac3,flac \
        --enable-decoder=h264,hevc,vp8,vp9,libdav1d,mpeg4,mjpeg,prores,mpeg2video,mpeg1video,h263 \
        --enable-decoder=msmpeg4v3,dvvideo,theora \
        --enable-decoder=aac,mp3,opus,vorbis,flac,alac,ac3,eac3,pcm_s16le \
        --enable-encoder=png --enable-muxer=image2pipe \
        --enable-filter=buffer,buffersink,abuffer,abuffersink,aformat,anull,aresample \
        --enable-filter=scale,format,null,transpose,hflip,vflip,rotate,fps,select,setpts,trim,tile \
    && make -j"$(nproc)" \
    && make install

# --- The server
# The Debian release is named, so a rebuild does not switch the system underneath.
FROM docker.io/library/python:3.14-slim-trixie

# No .pyc files in the image, log lines appear at once, no pip cache or update checks
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# FFmpeg reads and decodes videos; without it the server takes images only. Its libraries are in the two
# programs; dav1d (AV1) is the one it loads.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libdav1d7 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/

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
