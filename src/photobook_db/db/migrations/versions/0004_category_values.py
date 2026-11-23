# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Categories get fixed values, like ratings: photo and artwork, and every category that is a tag already, so no
image's category becomes one that cannot be given again."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORY_NAMESPACE = "category"
DEFAULT_CATEGORIES = ["photo", "artwork"]


def upgrade() -> None:
    connection = op.get_bind()
    namespace = {"namespace": CATEGORY_NAMESPACE}
    connection.execute(sa.text("UPDATE namespace SET fixed_values = 1 WHERE name = :namespace"), namespace)
    for category in DEFAULT_CATEGORIES:
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO tag (namespace_id, name) "
                "SELECT id, :category FROM namespace WHERE name = :namespace"
            ),
            {"category": category} | namespace,
        )


def downgrade() -> None:
    connection = op.get_bind()
    namespace = {"namespace": CATEGORY_NAMESPACE}
    connection.execute(sa.text("UPDATE namespace SET fixed_values = 0 WHERE name = :namespace"), namespace)
    # Values that no image has: before, a category was a tag only once an image had it.
    connection.execute(
        sa.text(
            "DELETE FROM tag WHERE namespace_id = (SELECT id FROM namespace WHERE name = :namespace) "
            "AND id NOT IN (SELECT tag_id FROM image_tag)"
        ),
        namespace,
    )
