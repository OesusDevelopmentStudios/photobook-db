# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Videos are rows of the image table: their kind, duration, codecs, bitrate and frame rate, and their own recording
time as a date source. The new date source changes a CHECK constraint, which SQLite can only do by copying the
table; the migration runs with foreign keys off for that (db/_migrations.py)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_CHECK = "ck_image_taken_at_source"
KIND_CHECK = "ck_image_media_kind"
VIDEO_COLUMNS = ("duration_ms", "video_codec", "audio_codec", "bitrate", "frame_rate")


def upgrade() -> None:
    with op.batch_alter_table("image", recreate="always") as batch:
        batch.drop_constraint(SOURCE_CHECK, type_="check")
        batch.create_check_constraint(
            SOURCE_CHECK, "taken_at_source IN ('exif', 'file_mtime', 'import_time', 'container')"
        )
        batch.add_column(sa.Column("media_kind", sa.String(), nullable=False, server_default="image"))
        batch.create_check_constraint(KIND_CHECK, "media_kind IN ('image', 'video')")
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("video_codec", sa.String(), nullable=True))
        batch.add_column(sa.Column("audio_codec", sa.String(), nullable=True))
        batch.add_column(sa.Column("bitrate", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("frame_rate", sa.Float(), nullable=True))


def downgrade() -> None:
    # A server of the version before knows no videos: going back with videos stored would lose them.
    videos = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM image WHERE media_kind = 'video'")).scalar()
    if videos:
        raise RuntimeError(f"{videos} videos are stored; the schema before 0006 cannot hold them")

    with op.batch_alter_table("image", recreate="always") as batch:
        batch.drop_constraint(KIND_CHECK, type_="check")
        for column in ("media_kind", *VIDEO_COLUMNS):
            batch.drop_column(column)
        batch.drop_constraint(SOURCE_CHECK, type_="check")
        batch.create_check_constraint(SOURCE_CHECK, "taken_at_source IN ('exif', 'file_mtime', 'import_time')")
