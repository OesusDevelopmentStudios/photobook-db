# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import tomllib
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import photobook_db

PROJECT_DIR = Path(__file__).parent.parent


def pyproject() -> dict[str, Any]:
    return tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_matches_pyproject() -> None:
    assert photobook_db.__version__ == pyproject()["project"]["version"]


def test_installed_package_has_every_template_and_static_file() -> None:
    """An editable install reads the source folder, so a file missing from package-data only fails in a
    real install, e.g. the container image."""
    patterns = pyproject()["tool"]["setuptools"]["package-data"]["photobook_db.admin_web"]
    admin_web = PROJECT_DIR / "src" / "photobook_db" / "admin_web"

    for file in [*(admin_web / "templates").iterdir(), *(admin_web / "static").iterdir()]:
        relative = file.relative_to(admin_web).as_posix()
        assert any(fnmatch(relative, pattern) for pattern in patterns), f"{relative} is not installed"
