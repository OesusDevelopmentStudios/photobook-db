# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The built-in namespace "meta": tags the server sets from the file itself, such as its kind and type. Users
cannot edit them, so namespaces now say whether users may."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

META_NAMESPACE = "meta"

namespace_table = sa.table(
    "namespace",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String()),
    sa.column("single_value", sa.Boolean()),
    sa.column("is_builtin", sa.Boolean()),
    sa.column("user_editable", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    op.add_column("namespace", sa.Column("user_editable", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.bulk_insert(
        namespace_table,
        [
            {
                "name": META_NAMESPACE,
                "single_value": False,  # a file has a kind, a type, a size class…
                "is_builtin": True,
                "user_editable": False,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            }
        ],
    )


def downgrade() -> None:
    meta_id = f"(SELECT id FROM namespace WHERE name = '{META_NAMESPACE}')"
    op.execute(f"DELETE FROM image_tag WHERE tag_id IN (SELECT id FROM tag WHERE namespace_id = {meta_id})")
    op.execute(f"DELETE FROM tag WHERE namespace_id = {meta_id}")
    op.execute(f"DELETE FROM namespace WHERE name = '{META_NAMESPACE}'")
    # Not in batch mode: that rebuilds the table, and with foreign keys on, dropping the old one fails while
    # tags refer to it. SQLite drops a column itself since 3.35.
    op.drop_column("namespace", "user_editable")
