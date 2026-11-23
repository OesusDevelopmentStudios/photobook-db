# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""When each tag was put on an image: an album's images are in the order they were added to it. Links from before
get their image's import time, the only date known for them."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_INDEX = "ix_image_tag_tag_id_added_at_image_id"


def upgrade() -> None:
    op.add_column("image_tag", sa.Column("added_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE image_tag SET added_at = (SELECT imported_at FROM image WHERE image.id = image_tag.image_id)")
    # SQLite cannot make a column NOT NULL in place. The rebuild is safe here: no table refers to image_tag.
    with op.batch_alter_table("image_tag") as batch:
        batch.alter_column("added_at", existing_type=sa.DateTime(), nullable=False)

    op.create_index(ORDER_INDEX, "image_tag", ["tag_id", "added_at", "image_id"])


def downgrade() -> None:
    op.drop_index(ORDER_INDEX, table_name="image_tag")
    op.drop_column("image_tag", "added_at")
