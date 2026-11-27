# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from photobook_db.admin_web._theme import readable_text_on


def _accent(request: Request) -> dict[str, Any]:
    """Every page, the login and error pages included, is drawn in the accent colour the admin chose."""
    accent_color = request.app.state.server_settings.web_accent_color()
    return {"accent_color": accent_color, "accent_text_color": readable_text_on(accent_color)}


_environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,  # every value inserted is escaped, so user input cannot inject markup
    undefined=StrictUndefined,  # a misspelled or renamed variable fails the page instead of showing nothing
)
templates = Jinja2Templates(env=_environment, context_processors=[_accent])
