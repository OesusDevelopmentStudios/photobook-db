# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The job worker with test jobs, on a real database and a real worker thread."""

import logging
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from photobook_db.db import Database
from photobook_db.db.models import Job
from photobook_db.services.auth import AuthService, Role, UserInfo
from photobook_db.services.jobs import JobContext, JobInfo, JobKind, JobsService, JobStatus
from photobook_db.utils.problems import Problem
from photobook_db.utils.time import utc_now

TEST_KIND = JobKind.RELAYOUT  # any kind will do; the tests register their own handlers


@pytest.fixture
def users(database: Database) -> dict[str, UserInfo]:
    auth = AuthService(database)
    return {
        "admin": auth.create_user("admin_user", "correct horse battery", Role.ADMIN),
        "restricted_user": auth.create_user("restricted_user", "correct horse battery", Role.USER),
        "teen": auth.create_user("teen", "correct horse battery", Role.USER),
    }


@pytest.fixture
def jobs(database: Database) -> Iterator[JobsService]:
    service = JobsService(database)
    yield service
    service.stop()


def count_items(context: JobContext) -> dict[str, Any]:
    total = context.params["items"]
    for item in range(total):
        context.report_progress(item + 1, total)

    return {"counted": total}


def wait_until_finished(jobs: JobsService, job_id: int, viewer: UserInfo, timeout_seconds: float = 5.0) -> JobInfo:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = jobs.get_job(job_id, viewer)
        if job.status in (JobStatus.DONE, JobStatus.FAILED):
            return job

        time.sleep(0.01)

    raise AssertionError(f"Job {job_id} did not finish in time")


# --- Running jobs

def test_runs_a_job_and_keeps_its_result(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    jobs.register(TEST_KIND, count_items)
    jobs.start()

    submitted = jobs.submit(TEST_KIND, {"items": 3}, users["restricted_user"].id)
    finished = wait_until_finished(jobs, submitted.id, users["restricted_user"])

    assert submitted.status == JobStatus.QUEUED
    assert (finished.status, finished.result, finished.error) == (JobStatus.DONE, {"counted": 3}, None)
    assert (finished.progress_done, finished.progress_total) == (3, 3)
    assert finished.finished_at is not None


def test_expected_error_is_kept_as_text(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    def refuse(context: JobContext) -> dict[str, Any]:
        raise Problem(422, "validation_error", "Folder not found", "The import folder has no 'trip'.")

    jobs.register(TEST_KIND, refuse)
    jobs.start()

    finished = wait_until_finished(jobs, jobs.submit(TEST_KIND, {}, None).id, users["admin"])

    assert (finished.status, finished.error) == (JobStatus.FAILED, "Folder not found. The import folder has no 'trip'.")


def test_unexpected_error_gives_a_trace_id_in_the_log(
    jobs: JobsService, users: dict[str, UserInfo], caplog: pytest.LogCaptureFixture
) -> None:
    def crash(context: JobContext) -> dict[str, Any]:
        raise RuntimeError("bug")

    jobs.register(TEST_KIND, crash)
    jobs.start()

    with caplog.at_level(logging.ERROR):
        finished = wait_until_finished(jobs, jobs.submit(TEST_KIND, {}, None).id, users["admin"])

    trace_id = finished.error.split("trace id ")[1].split(" ")[0]
    assert finished.status == JobStatus.FAILED
    assert trace_id in caplog.text


def test_job_without_handler_fails(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    jobs.start()

    finished = wait_until_finished(jobs, jobs.submit(JobKind.FOLDER_IMPORT, {}, None).id, users["admin"])

    assert finished.status == JobStatus.FAILED
    assert "No handler" in finished.error


def test_runs_one_job_at_a_time_oldest_first(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    order: list[int] = []
    running = threading.Lock()

    def record(context: JobContext) -> dict[str, Any]:
        assert running.acquire(blocking=False), "two jobs ran at the same time"
        order.append(context.params["number"])
        time.sleep(0.02)
        running.release()
        return {}

    jobs.register(TEST_KIND, record)
    submitted = [jobs.submit(TEST_KIND, {"number": number}, None) for number in range(4)]
    jobs.start()

    for job in submitted:
        assert wait_until_finished(jobs, job.id, users["admin"]).status == JobStatus.DONE

    assert order == [0, 1, 2, 3]


# --- Stopping and restarting

def test_stopping_queues_the_running_job_again(database: Database, users: dict[str, UserInfo]) -> None:
    started = threading.Event()

    def endless(context: JobContext) -> dict[str, Any]:
        started.set()
        while True:
            context.report_progress(0, None)  # raises StopRequested once the server stops
            time.sleep(0.01)

    first_run = JobsService(database)
    first_run.register(TEST_KIND, endless)
    first_run.start()
    job = first_run.submit(TEST_KIND, {"items": 2}, None)
    assert started.wait(5)

    first_run.stop()

    assert first_run.get_job(job.id, users["admin"]).status == JobStatus.QUEUED
    second_run = JobsService(database)
    second_run.register(TEST_KIND, count_items)
    second_run.start()
    assert wait_until_finished(second_run, job.id, users["admin"]).status == JobStatus.DONE
    second_run.stop()


def test_job_left_running_by_a_crash_runs_again(
    jobs: JobsService, database: Database, users: dict[str, UserInfo]
) -> None:
    job = jobs.submit(TEST_KIND, {"items": 1}, None)
    with database.session() as session:  # as if the server had crashed while running it
        session.execute(update(Job).where(Job.id == job.id).values(status=JobStatus.RUNNING))
        session.commit()

    jobs.register(TEST_KIND, count_items)
    jobs.start()

    assert wait_until_finished(jobs, job.id, users["admin"]).status == JobStatus.DONE


# --- Who sees which jobs

def test_users_see_only_their_own_jobs(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    restricted_users_job = jobs.submit(TEST_KIND, {}, users["restricted_user"].id)
    teens_job = jobs.submit(TEST_KIND, {}, users["teen"].id)

    assert jobs.get_job(restricted_users_job.id, users["restricted_user"]).id == restricted_users_job.id
    with pytest.raises(Problem) as raised:
        jobs.get_job(teens_job.id, users["restricted_user"])

    assert raised.value.status == 404
    assert [job.id for job in jobs.list_jobs(users["restricted_user"])] == [restricted_users_job.id]
    assert [job.id for job in jobs.list_jobs(users["admin"])] == [teens_job.id, restricted_users_job.id]  # newest first


def test_lists_jobs_by_status(jobs: JobsService, users: dict[str, UserInfo]) -> None:
    jobs.register(TEST_KIND, count_items)
    done = jobs.submit(TEST_KIND, {"items": 1}, None)
    jobs.start()
    wait_until_finished(jobs, done.id, users["admin"])
    jobs.stop()
    queued = jobs.submit(TEST_KIND, {"items": 1}, None)

    assert [job.id for job in jobs.list_jobs(users["admin"], JobStatus.DONE)] == [done.id]
    assert [job.id for job in jobs.list_jobs(users["admin"], JobStatus.QUEUED)] == [queued.id]


# --- Removing finished jobs

def job_in_state(database: Database, jobs: JobsService, status: JobStatus, finished_hours_ago: float | None) -> int:
    job_id = jobs.submit(TEST_KIND, {}, None).id  # the worker is not started: the state stays as set here
    finished_at = None if finished_hours_ago is None else utc_now() - timedelta(hours=finished_hours_ago)
    with database.session() as session:
        session.execute(update(Job).where(Job.id == job_id).values(status=status, finished_at=finished_at))
        session.commit()

    return job_id


def test_removes_successful_jobs_a_day_after_they_finished(
    database: Database, jobs: JobsService, users: dict[str, UserInfo]
) -> None:
    old_success = job_in_state(database, jobs, JobStatus.DONE, finished_hours_ago=25)
    recent_success = job_in_state(database, jobs, JobStatus.DONE, finished_hours_ago=23)
    old_failure = job_in_state(database, jobs, JobStatus.FAILED, finished_hours_ago=25)
    waiting = job_in_state(database, jobs, JobStatus.QUEUED, finished_hours_ago=None)

    assert jobs.remove_expired_jobs() == 1
    remaining = {job.id for job in jobs.list_jobs(users["admin"])}
    assert old_success not in remaining
    assert remaining == {recent_success, old_failure, waiting}


def test_worker_removes_expired_jobs_when_it_starts(
    database: Database, jobs: JobsService, users: dict[str, UserInfo]
) -> None:
    job_in_state(database, jobs, JobStatus.DONE, finished_hours_ago=25)

    jobs.start()

    deadline = time.monotonic() + 5
    while jobs.list_jobs(users["admin"]) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs.list_jobs(users["admin"]) == []


def test_removes_every_finished_job_on_request(
    database: Database, jobs: JobsService, users: dict[str, UserInfo]
) -> None:
    job_in_state(database, jobs, JobStatus.DONE, finished_hours_ago=1)
    job_in_state(database, jobs, JobStatus.FAILED, finished_hours_ago=1)
    running = job_in_state(database, jobs, JobStatus.RUNNING, finished_hours_ago=None)
    waiting = job_in_state(database, jobs, JobStatus.QUEUED, finished_hours_ago=None)

    assert jobs.remove_finished_jobs() == 2
    assert {job.id for job in jobs.list_jobs(users["admin"])} == {running, waiting}
