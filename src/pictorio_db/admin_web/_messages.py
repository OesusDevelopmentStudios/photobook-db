# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pictorio_db.utils.problems import Problem


def problem_message(problem: Problem) -> str:
    """A service error as a sentence for a form. Validation problems say which field is wrong and why."""
    field_errors = problem.extra.get("errors", []) if problem.code == "validation_error" else []
    if field_errors:
        return " ".join(f"{_field_label(error['field'])}: {error['message'].rstrip('.')}." for error in field_errors)

    return f"{problem.title}. {problem.detail or ''}".strip()


def _field_label(field: str) -> str:
    return field.removeprefix("body.").replace("_", " ").capitalize()  # body.path_template → Path template
