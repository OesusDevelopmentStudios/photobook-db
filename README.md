# photobook-db

[![CI](https://github.com/OesusDevelopmentStudios/photobook-db/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/OesusDevelopmentStudios/photobook-db/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/tag/OesusDevelopmentStudios/photobook-db?sort=semver&label=release)](https://github.com/OesusDevelopmentStudios/photobook-db/tags)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FOesusDevelopmentStudios%2Fphotobook-db%2Fmaster%2Fpyproject.toml)](pyproject.toml)
[![Container image](https://img.shields.io/badge/container-ghcr.io-blue?logo=podman)](https://github.com/OesusDevelopmentStudios/photobook-db/pkgs/container/photobook-db)

Image library server for [Photobook](https://github.com/OesusDevelopmentStudios/photobook).

photobook-db stores your images in a library folder, arranges them by their tags and keeps a database of tags and metadata. The Photobook desktop app connects to it to search, browse, tag and upload images. Configuration is available via web interface.

This server is designed to run both locally or on a remote server.

## Install

Please refer to the [install guide](docs/install-guide.md)

## Development

Needs Python 3.12 or newer. Development instructions and rules are available in [CONTRIBUTING.md](CONTRIBUTING.md).
