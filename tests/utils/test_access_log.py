# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import logging

from fastapi.testclient import TestClient

from photobook_db.utils.access_log import HideSignatures


def access_line(path: str) -> str:
    """Formatted as Uvicorn's access log formats a request."""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", "GET", path, "1.1", 206), None,
    )
    HideSignatures().filter(record)
    return record.getMessage()


def test_hides_the_signature_of_a_stream_url() -> None:
    line = access_line("/api/v1/images/12/file?t=3&exp=1795000000&sig=AbC-123_x")

    assert line == '127.0.0.1:50000 - "GET /api/v1/images/12/file?t=3&exp=1795000000&sig=(hidden) HTTP/1.1" 206'


def test_leaves_other_requests_alone() -> None:
    assert "?q=signature&limit=5" in access_line("/api/v1/images?q=signature&limit=5")


def test_the_server_hides_them_from_its_start(client: TestClient) -> None:
    assert any(isinstance(found, HideSignatures) for found in logging.getLogger("uvicorn.access").filters)
