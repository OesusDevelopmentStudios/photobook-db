# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import re

from fastapi.testclient import TestClient

from pictorio_db.services.server_settings import ServerSettings, SettingsChanges

from .conftest import csrf_token_in

MIB = 1024 * 1024


def save(browser: TestClient, **fields: str):  # -> Response
    """Sends the form as the page shows it, with the given fields changed."""
    form = {
        "csrf_token": csrf_token_in(browser.get("/admin/settings").text),
        "path_template": "{category}/{author}/{year}",
        "max_upload_mib": "200",
        "rating_values": "sfw\nnsfw",
    }
    return browser.post("/admin/settings", data=form | fields, follow_redirects=False)


def server_settings(browser: TestClient) -> ServerSettings:
    return browser.app.state.server_settings


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/settings", follow_redirects=False).status_code == 303


def test_shows_the_settings_and_the_folders(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/settings").text

    assert 'value="{category}/{author}/{year}"' in page
    assert 'value="200"' in page
    assert ">sfw\nnsfw</textarea>" in page
    assert str(server_settings(admin_browser).current().library_dir) in page
    assert "folder imports are off" in page


def test_saves_the_upload_limit_and_rating_values(admin_browser: TestClient) -> None:
    response = save(admin_browser, max_upload_mib="50", rating_values="sfw\n\n Very Safe \n")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/settings?saved=true")
    settings = server_settings(admin_browser).current()
    assert (settings.max_upload_bytes, settings.rating_values) == (50 * MIB, ["sfw", "very_safe"])
    assert admin_browser.app.state.jobs.list_jobs(admin_browser.app.state.auth.list_users()[0]) == []
    assert "The settings have been saved." in admin_browser.get(response.headers["location"]).text


def test_a_new_path_template_starts_a_relayout_job(admin_browser: TestClient) -> None:
    response = save(admin_browser, path_template="{category}/{year}")

    [job] = admin_browser.app.state.jobs.list_jobs(admin_browser.app.state.auth.list_users()[0])
    assert job.kind == "relayout"
    assert response.headers["location"] == f"/admin/settings?saved=true&relayout_job={job.id}"
    page = admin_browser.get(response.headers["location"]).text
    assert f'<a href="/admin/jobs/{job.id}">Job {job.id}</a> now moves the files' in page
    assert server_settings(admin_browser).current().path_template == "{category}/{year}"


def test_an_unchanged_limit_keeps_its_exact_bytes(admin_browser: TestClient) -> None:
    server_settings(admin_browser).change(SettingsChanges(max_upload_bytes=50 * MIB + 123), user_id=None)

    save(admin_browser, max_upload_mib="50", rating_values="sfw")

    assert server_settings(admin_browser).current().max_upload_bytes == 50 * MIB + 123


def test_invalid_values_are_shown_in_the_form_and_nothing_is_saved(admin_browser: TestClient) -> None:
    response = save(admin_browser, path_template="{category}/{nonsense}", rating_values="sfw")

    assert response.status_code == 422
    assert "Path template: Unknown placeholder {nonsense}" in response.text
    assert 'value="{category}/{nonsense}"' in response.text  # what was entered stays in the form
    settings = server_settings(admin_browser).current()
    assert (settings.path_template, settings.rating_values) == ("{category}/{author}/{year}", ["sfw", "nsfw"])


def test_problems_without_a_field_are_shown_too(admin_browser: TestClient) -> None:
    response = save(admin_browser, max_upload_mib="0")

    assert response.status_code == 422
    assert "Invalid upload limit. The limit must be from" in response.text


def test_needs_at_least_one_rating_value(admin_browser: TestClient) -> None:
    response = save(admin_browser, rating_values="\n  \n")

    assert response.status_code == 422
    assert "Give from 1 to 20 rating values." in response.text


# --- The accent colour of these pages

def choose_accent(browser: TestClient, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/settings").text)} | fields
    return browser.post("/admin/settings/accent", data=form, follow_redirects=False)


def pressed_swatches(page: str) -> list[str]:
    return re.findall(r'class="swatch" name="accent_color" value="(#[0-9a-f]{6})"[^>]*aria-pressed="true"', page)


def test_pages_use_the_plasma_blue_at_first(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/settings").text

    assert "--accent: #3daee9;" in page
    assert pressed_swatches(page) == ["#3daee9"]


def test_a_swatch_changes_the_accent_colour(admin_browser: TestClient) -> None:
    response = choose_accent(admin_browser, accent_color="#e93a9a")

    assert response.headers["location"] == "/admin/settings?accent_saved=true#appearance"
    assert server_settings(admin_browser).web_accent_color() == "#e93a9a"
    assert "--accent: #e93a9a;" in admin_browser.get("/admin").text


def test_a_custom_accent_colour_is_shown_as_such(admin_browser: TestClient) -> None:
    choose_accent(admin_browser, accent_color="custom", custom_color="#123456")

    page = admin_browser.get("/admin/settings").text
    assert server_settings(admin_browser).web_accent_color() == "#123456"
    assert "In use: <code>#123456</code>" in page
    assert pressed_swatches(page) == []


def test_an_invalid_colour_is_refused(admin_browser: TestClient) -> None:
    response = choose_accent(admin_browser, accent_color="custom", custom_color="blue")

    assert response.status_code == 422
    assert "Web accent color: A colour as #rrggbb" in response.text
    assert server_settings(admin_browser).web_accent_color() == "#3daee9"
