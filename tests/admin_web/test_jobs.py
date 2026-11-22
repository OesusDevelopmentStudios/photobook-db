# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import dataclasses
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from photobook_db.config import Config
from photobook_db.db.models import Job
from photobook_db.services.jobs import JobInfo, JobStatus
from photobook_db.services.server_settings import SettingsChanges

from .conftest import csrf_token_in

REFRESH = '<meta http-equiv="refresh" content="2">'


@pytest.fixture
def config(config: Config, tmp_path: Path) -> Config:
    """A server with an import folder: holiday/beach.jpg and an empty holiday/day_2/."""
    import_dir = tmp_path / "import"
    (import_dir / "holiday" / "day_2").mkdir(parents=True)
    Image.new("RGB", (8, 8), "teal").save(import_dir / "holiday" / "beach.jpg", format="JPEG")
    return dataclasses.replace(config, import_dir=import_dir)


def start_import(browser: TestClient, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/jobs").text)} | fields
    return browser.post("/admin/jobs/import", data=form, follow_redirects=False)


def finished(browser: TestClient, job_id: int) -> JobInfo:
    admin = browser.app.state.auth.list_users()[0]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = browser.app.state.jobs.get_job(job_id, admin)
        if job.status in (JobStatus.DONE, JobStatus.FAILED):
            return job

        time.sleep(0.01)

    raise AssertionError(f"Job {job_id} did not finish in time")


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/jobs", follow_redirects=False).status_code == 303


# --- Folder imports

def chosen_folder(page: str) -> list[str]:
    return re.findall(r'<option value="([^"]*)" selected>', page.split('name="source_subdir"')[1].split("</select>")[0])


def test_import_page_lists_the_folders(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/jobs/import").text

    assert re.findall(r'<option value="([^"]*)"', page.split('name="source_subdir"')[1].split("</select>")[0]) == [
        "", "holiday", "holiday/day_2"
    ]


def test_rescan_finds_new_folders_and_keeps_what_was_entered(admin_browser: TestClient, config: Config) -> None:
    assert config.import_dir is not None
    (config.import_dir / "camera").mkdir()

    response = start_import(
        admin_browser, action="rescan", source_subdir="holiday", tag_general="summer", remove_originals="true"
    )

    assert response.status_code == 200
    assert '<option value="camera">camera</option>' in response.text
    assert chosen_folder(response.text) == ["holiday"]
    assert ">summer</textarea>" in response.text and " checked>" in response.text
    assert admin_browser.app.state.jobs.list_jobs(admin_browser.app.state.auth.list_users()[0]) == []


def test_import_page_has_a_tag_field_per_namespace(admin_browser: TestClient) -> None:
    admin_browser.app.state.tags.create_namespace("weather", single_value=True)
    location = start_import(admin_browser, source_subdir="holiday", tag_category="photo").headers["location"]
    finished(admin_browser, int(location.rsplit("/", 1)[1]))  # now an image has category:photo

    page = admin_browser.get("/admin/jobs/import").text

    assert re.findall(r'name="tag_(\w+)"', page) == [
        "category", "author", "character", "rating", "album", "general", "weather"
    ]
    assert '<select id="tag_rating" name="tag_rating">' in page and '<option value="private">private</option>' in page
    assert '<textarea id="tag_author" name="tag_author" rows="1"' in page  # several tags: a box that can grow
    assert re.search(r'<datalist id="tag_category-values">\s*<option value="photo">', page)
    assert 'list="tag_weather-values"' in page  # every one-tag-per-image namespace gets suggestions


def test_imports_a_folder_and_shows_the_result(admin_browser: TestClient) -> None:
    response = start_import(
        admin_browser,
        source_subdir=" holiday ",
        tag_author=" sample_artist,\n\n Other Artist ",
        tag_rating="public",
        tag_general="summer",
        tag_album="",
    )

    job_id = int(response.headers["location"].removeprefix("/admin/jobs/"))
    job = finished(admin_browser, job_id)
    tags = ["author:sample_artist", "author:Other Artist", "rating:public", "general:summer"]
    assert job.params == {"source_subdir": "holiday", "remove_originals": False, "tags": tags}
    page = admin_browser.get(f"/admin/jobs/{job_id}").text
    assert "Import of <code>holiday</code>" in page
    assert '<span class="status status-done">Done</span>' in page
    assert "<dt>Imported</dt><dd>1</dd>" in page
    assert REFRESH not in page  # finished: nothing more to show
    assert f'<a href="/admin/jobs/{job_id}">{job_id}</a>' in admin_browser.get("/admin/jobs").text
    assert f'<a href="/admin/jobs/{job_id}">{job_id}</a>' in admin_browser.get("/admin").text


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"source_subdir": "nowhere"}, "Folder not found. The import folder has no &#39;nowhere&#39;."),
        ({"source_subdir": "../outside"}, "Invalid folder."),
        ({"source_subdir": "holiday", "tag_general": "a|b"}, "Invalid tag."),
        ({"source_subdir": "holiday", "tag_category": "photo, artwork"}, "An image has one category tag, so give one."),
    ],
    ids=["missing folder", "outside the import folder", "invalid tag", "two tags in a one-tag field"],
)
def test_import_that_cannot_start_shows_why(admin_browser: TestClient, fields: dict[str, str], message: str) -> None:
    response = start_import(admin_browser, remove_originals="true", **fields)

    assert response.status_code == 422
    assert message in response.text
    assert chosen_folder(response.text) == [fields["source_subdir"]] and " checked>" in response.text
    assert admin_browser.app.state.jobs.list_jobs(admin_browser.app.state.auth.list_users()[0]) == []


# --- Progress

def test_unfinished_job_page_reloads_itself(admin_browser: TestClient) -> None:
    admin_browser.app.state.jobs.stop()  # the job stays queued
    job_id = int(start_import(admin_browser, source_subdir="holiday").headers["location"].rsplit("/", 1)[1])

    page = admin_browser.get(f"/admin/jobs/{job_id}").text

    assert REFRESH in page
    assert '<span class="status status-queued">Waiting</span>' in page
    assert REFRESH in admin_browser.get("/admin/jobs").text


def test_failed_job_shows_why(admin_browser: TestClient) -> None:
    admin_browser.app.state.jobs.stop()
    job_id = int(start_import(admin_browser, source_subdir="holiday").headers["location"].rsplit("/", 1)[1])
    with admin_browser.app.state.database.session() as session:
        session.execute(update(Job).values(status=JobStatus.FAILED, error="The disk is full."))
        session.commit()

    page = admin_browser.get(f"/admin/jobs/{job_id}").text

    assert "The job failed: The disk is full." in page
    assert '<span class="status status-failed">Failed</span>' in page


def test_relayout_job_is_linked_from_the_settings(admin_browser: TestClient) -> None:
    _settings, job = admin_browser.app.state.server_settings.change(
        SettingsChanges(path_template="{category}/{year}"), None
    )
    assert job is not None

    finished(admin_browser, job.id)
    page = admin_browser.get(f"/admin/jobs/{job.id}").text

    assert "Moving files to the path template" in page
    assert "<dt>Moved</dt><dd>0</dd>" in page


def start_removal(browser: TestClient):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/jobs").text)}
    return browser.post("/admin/jobs/remove-finished", data=form, follow_redirects=False)


def test_removes_the_finished_jobs(admin_browser: TestClient) -> None:
    job_id = int(start_import(admin_browser, source_subdir="holiday").headers["location"].rsplit("/", 1)[1])
    finished(admin_browser, job_id)
    assert "24 hours after they finish." in admin_browser.get("/admin/jobs").text

    response = start_removal(admin_browser)

    assert (response.status_code, response.headers["location"]) == (303, "/admin/jobs?removed=1")
    page = admin_browser.get(response.headers["location"]).text
    assert "1 finished job removed." in page
    assert '<span class="disabled-reason" title="There are no finished jobs.">' in page


def test_unknown_job_is_a_404_page(admin_browser: TestClient) -> None:
    response = admin_browser.get("/admin/jobs/999")

    assert response.status_code == 404
    assert "This job does not exist." in response.text


class TestWithoutAnImportFolder:
    @pytest.fixture
    def config(self, config: Config) -> Config:
        return dataclasses.replace(config, import_dir=None)

    def test_import_button_says_why_it_is_off(self, admin_browser: TestClient) -> None:
        page = admin_browser.get("/admin/jobs").text

        assert 'title="Folder imports are off. Set PHOTOBOOK_IMPORT_DIR on the server to turn them on."' in page
        assert '<button type="button" disabled>+ Import Folder</button>' in page

    def test_import_page_explains_how_to_turn_imports_on(self, admin_browser: TestClient) -> None:
        page = admin_browser.get("/admin/jobs/import").text

        assert "Folder imports are off." in page
        assert 'name="source_subdir"' not in page
