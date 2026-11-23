# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Namespaces with fixed values, such as "rating": the values are the namespace's tags, so every value a list in
the settings named becomes a tag, also with no image, and the setting goes."""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RATING_NAMESPACE = "rating"
RATING_VALUES_SETTING = "rating_values"
DEFAULT_RATING_VALUES = ["public", "private"]  # what a server without the setting allowed


def upgrade() -> None:
    op.add_column("namespace", sa.Column("fixed_values", sa.Boolean(), nullable=False, server_default=sa.false()))
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE namespace SET fixed_values = 1 WHERE name = :name"), {"name": RATING_NAMESPACE})

    stored = connection.execute(
        sa.text("SELECT value FROM setting WHERE key = :key"), {"key": RATING_VALUES_SETTING}
    ).scalar()
    values = DEFAULT_RATING_VALUES if stored is None else json.loads(stored)
    for value in values:  # in their order: the tags' ids keep it
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO tag (namespace_id, name) "
                "SELECT id, :value FROM namespace WHERE name = :namespace"
            ),
            {"value": value, "namespace": RATING_NAMESPACE},
        )

    connection.execute(sa.text("DELETE FROM setting WHERE key = :key"), {"key": RATING_VALUES_SETTING})


def downgrade() -> None:
    connection = op.get_bind()
    values = connection.execute(
        sa.text(
            "SELECT tag.name FROM tag JOIN namespace ON namespace.id = tag.namespace_id "
            "WHERE namespace.name = :namespace ORDER BY tag.id"
        ),
        {"namespace": RATING_NAMESPACE},
    ).scalars().all()
    connection.execute(
        sa.text("INSERT OR REPLACE INTO setting (key, value) VALUES (:key, :value)"),
        {"key": RATING_VALUES_SETTING, "value": json.dumps(list(values))},
    )
    # Values that no image has were only values before; as tags they would be suggested to users.
    connection.execute(
        sa.text(
            "DELETE FROM tag WHERE namespace_id = (SELECT id FROM namespace WHERE name = :namespace) "
            "AND id NOT IN (SELECT tag_id FROM image_tag)"
        ),
        {"namespace": RATING_NAMESPACE},
    )
    op.drop_column("namespace", "fixed_values")
