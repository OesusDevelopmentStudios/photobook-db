# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError

from photobook_db.admin_web._templates import templates


# A renamed attribute once left a column of the namespace page empty without any test noticing.
def test_a_missing_value_fails_the_page_instead_of_showing_nothing() -> None:
    template = templates.env.from_string("<td>{{ match.image_count }}</td>")

    with pytest.raises(UndefinedError):
        template.render(match=SimpleNamespace(media_count=3))


def test_a_value_may_say_what_stands_in_for_it_when_missing() -> None:
    assert templates.env.from_string("{{ result.too_large | default(0) }}").render(result={}) == "0"


def test_values_are_still_escaped() -> None:
    assert templates.env.from_string("{{ text }}").render(text="<b>&") == "&lt;b&gt;&amp;"
