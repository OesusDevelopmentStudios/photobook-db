# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest
from fastapi.testclient import TestClient

ICON_URL = "/admin/static/icon.png"


def test_icon_is_served_without_a_login(client: TestClient) -> None:
    response = client.get(ICON_URL)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


@pytest.mark.parametrize("path", ["/admin/login", "/admin/setup"])
def test_pages_before_the_login_show_the_icon(client: TestClient, path: str) -> None:
    html = client.get(path).text

    assert f'<link rel="icon" type="image/png" href="{ICON_URL}">' in html


def test_pages_behind_the_login_show_the_icon_in_the_top_bar(admin_browser: TestClient) -> None:
    html = admin_browser.get("/admin").text

    assert f'<link rel="icon" type="image/png" href="{ICON_URL}">' in html
    assert f'<img src="{ICON_URL}"' in html
