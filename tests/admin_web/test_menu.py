# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The top bar of the pages behind the login: the tabs, and the user menu with Settings and Log out."""

import re

from fastapi.testclient import TestClient

from .conftest import csrf_token_in


def tabs_in(html: str) -> list[str]:
    tabs = re.search(r'<div class="tabs">(.*?)</div>', html, re.DOTALL)
    assert tabs is not None, "the page has no tabs"
    return re.findall(r">([^<]+)</a>", tabs.group(1))


def user_menu_in(html: str) -> str:
    menu = re.search(r'<details class="user-menu">(.*?)</details>', html, re.DOTALL)
    assert menu is not None, "the page has no user menu"
    return menu.group(1)


def test_tabs_leave_settings_to_the_user_menu(admin_browser: TestClient) -> None:
    html = admin_browser.get("/admin").text

    assert tabs_in(html) == ["Overview", "User Management", "Namespaces", "Tags", "Jobs"]


def test_user_menu_has_settings_and_log_out_under_the_username(admin_browser: TestClient) -> None:
    menu = user_menu_in(admin_browser.get("/admin").text)

    assert "<summary>admin_user</summary>" in menu
    assert '<a href="/admin/settings">Settings</a>' in menu
    assert '<form method="post" action="/admin/logout">' in menu
    assert ">Log out</button>" in menu


def test_settings_page_marks_the_user_menu_as_current(admin_browser: TestClient) -> None:
    html = admin_browser.get("/admin/settings").text

    menu = user_menu_in(html)
    assert '<summary class="current">admin_user</summary>' in menu
    assert '<a href="/admin/settings" aria-current="page">Settings</a>' in menu
    assert "aria-current" not in html.split('<div class="tabs">')[1].split("</div>")[0]


def test_log_out_in_the_user_menu_ends_the_session(admin_browser: TestClient) -> None:
    menu = user_menu_in(admin_browser.get("/admin/users").text)

    response = admin_browser.post("/admin/logout", data={"csrf_token": csrf_token_in(menu)}, follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, "/admin/login")
    assert admin_browser.get("/admin", follow_redirects=False).status_code == 303
