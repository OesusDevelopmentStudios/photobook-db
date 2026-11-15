# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""One admin account per server. Older databases could have several: the oldest one stays admin and the
others become standard users, with a warning in the log."""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = logging.getLogger("pictorio_db.db.migrations")


def upgrade() -> None:
    connection = op.get_bind()
    admins = connection.execute(
        sa.text("SELECT id, username FROM user WHERE role = 'admin' ORDER BY created_at, id")
    ).all()
    for user_id, username in admins[1:]:
        connection.execute(sa.text("UPDATE user SET role = 'user' WHERE id = :id"), {"id": user_id})
        _logger.warning(
            "Only one admin account is allowed now: %r stays admin, %r became a standard user.",
            admins[0].username,
            username,
        )

    op.create_index("ix_user_role", "user", ["role"], unique=True, sqlite_where=sa.text("role = 'admin'"))


def downgrade() -> None:
    op.drop_index("ix_user_role", table_name="user")  # demoted admins stay standard users
