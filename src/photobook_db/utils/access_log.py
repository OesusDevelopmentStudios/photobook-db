# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Keeps signed stream URLs out of the server's log: Uvicorn's access log writes each request's query."""

import logging
import re

_SIGNATURE = re.compile(r"(?<=[?&]sig=)[^&\s]*")


class HideSignatures(logging.Filter):
    """Writes sig=… as sig=(hidden); the rest of the line stays, so requests can still be followed."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SIGNATURE.sub("(hidden)", argument) if isinstance(argument, str) else argument
                for argument in record.args
            )

        return True


def hide_signatures_in_access_log() -> None:
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, HideSignatures) for existing in access_log.filters):
        access_log.addFilter(HideSignatures())
