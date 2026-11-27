# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from photobook_db.db import Database
from photobook_db.services.settings import SettingsService
from photobook_db.services.stream import SIGNING_KEY_SETTING, StreamService
from photobook_db.utils.problems import Problem

NOW = datetime(2026, 11, 27, 12, 0)  # UTC without a time zone, as the server's clock gives it


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def settings(database: Database) -> SettingsService:
    return SettingsService(database)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def streams(settings: SettingsService, clock: Clock) -> StreamService:
    return StreamService(settings, now=clock)


def refused(action: object) -> Problem:
    with pytest.raises(Problem) as raised:
        action()  # type: ignore[operator]
    return raised.value


def test_a_grant_lasts_twelve_hours(streams: StreamService) -> None:
    grant = streams.grant(image_id=7, token_id=3)

    assert grant.expires_at == datetime(2026, 11, 28, 0, 0, tzinfo=UTC)
    assert grant.query() == f"t=3&exp={grant.expires}&sig={grant.signature}"
    streams.check(7, 3, grant.expires, grant.signature)


def test_expires(streams: StreamService, clock: Clock) -> None:
    grant = streams.grant(7, 3)
    clock.now = NOW + timedelta(hours=12)

    problem = refused(lambda: streams.check(7, 3, grant.expires, grant.signature))

    assert (problem.status, problem.code) == (401, "not_authenticated")


def other_character(text: str) -> str:
    return text[:-1] + ("B" if text[-1] == "A" else "A")


# What the URL says: (file, login, expiry, signature); each case changes one of them.
CHANGES = {
    "another file": lambda image, token, expires, signature: (image + 1, token, expires, signature),
    "another login": lambda image, token, expires, signature: (image, token + 1, expires, signature),
    "a later expiry": lambda image, token, expires, signature: (image, token, expires + 3600, signature),
    "a changed signature": lambda image, token, expires, signature: (image, token, expires, other_character(signature)),
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_signature_covers_everything(streams: StreamService, change: str) -> None:
    grant = streams.grant(7, 3)
    changed = CHANGES[change](7, 3, grant.expires, grant.signature)

    assert refused(lambda: streams.check(*changed)).code == "not_authenticated"


def test_the_key_is_made_once_and_kept(settings: SettingsService, clock: Clock) -> None:
    grant = StreamService(settings, now=clock).grant(7, 3)

    StreamService(settings, now=clock).check(7, 3, grant.expires, grant.signature)  # e.g. after a restart
    assert len(settings.get(SIGNING_KEY_SETTING)) >= 43  # 32 random bytes


class SlowSettings(SettingsService):
    """Slow to come back from a read, so requests at once all read before any of them has stored a key."""

    def get(self, key: str) -> object:
        found = super().get(key)
        time.sleep(0.05)
        return found


def test_first_grants_at_once_share_one_key(database: Database, clock: Clock) -> None:
    streams = StreamService(SlowSettings(database), now=clock)
    grants = []
    threads = [threading.Thread(target=lambda: grants.append(streams.grant(7, 3))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(grants) == 4
    assert len({grant.signature for grant in grants}) == 1
