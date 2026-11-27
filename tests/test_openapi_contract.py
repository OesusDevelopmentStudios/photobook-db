# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The API schema is committed as docs/openapi.json, so every API change shows up in review."""

import json
from pathlib import Path

from photobook_db.app import create_app
from photobook_db.config import Config

SCHEMA_PATH = Path(__file__).parent.parent / "docs" / "openapi.json"


def test_the_api_calls_them_media(config: Config) -> None:
    """Paths, parameters, operations, tags, schemas and fields; an image is only a media kind or a content type."""
    schema = create_app(config).openapi()
    operations = [operation for path_item in schema["paths"].values() for operation in path_item.values()]
    names = [
        *schema["paths"],
        *(operation["operationId"] for operation in operations),
        *(tag for operation in operations for tag in operation.get("tags", [])),
        *(parameter["name"] for operation in operations for parameter in operation.get("parameters", [])),
        *schema["components"]["schemas"],
        *(field for model in schema["components"]["schemas"].values() for field in model.get("properties", {})),
    ]

    assert [name for name in names if "image" in name.lower()] == []


def test_api_matches_committed_schema(config: Config) -> None:
    committed_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert create_app(config).openapi() == committed_schema, (
        "The API differs from docs/openapi.json. If the change is intended, update the file with:\n"
        "    photobook-db openapi > docs/openapi.json"
    )
