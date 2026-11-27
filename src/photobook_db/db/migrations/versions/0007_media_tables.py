# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""What the server stores is media, photos and videos: the tables image and image_tag become media and media_tag,
and image_id becomes media_id, also in file_move. The tables are made anew and the rows copied, so that every
constraint and index is named after its new table too; going back copies them into the old names."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The columns of the media table, in its order
COLUMNS = (
    "id", "sha256", "storage_path", "original_filename", "mime_type", "width", "height", "file_size", "taken_at",
    "taken_at_source", "taken_at_utc_offset_minutes", "imported_at", "updated_at", "imported_by_user_id", "revision",
    "media_kind", "duration_ms", "video_codec", "audio_codec", "bitrate", "frame_rate",
)
NEW_FILE_MOVE = "file_move_new"
# The final names of the CHECK constraints (op.f: the naming convention is not applied again). 0006 named its own
# twice over, ck_image_ck_image_…; going back restores exactly those.
CHECK_NAMES = {
    "media": ("ck_media_taken_at_source", "ck_media_media_kind"),
    "image": ("ck_image_ck_image_taken_at_source", "ck_image_ck_image_media_kind"),
}


def upgrade() -> None:
    _move_rows(old="image", new="media")


def downgrade() -> None:
    _move_rows(old="media", new="image")


def _move_rows(old: str, new: str) -> None:
    """Makes the tables under the new name, copies every row into them and drops the old ones."""
    _create_tables(new)
    columns = ", ".join(COLUMNS)
    op.execute(f"INSERT INTO {new} ({columns}) SELECT {columns} FROM {old}")
    op.execute(f"INSERT INTO {new}_tag ({new}_id, tag_id, added_at) SELECT {old}_id, tag_id, added_at FROM {old}_tag")
    op.execute(
        f"INSERT INTO {NEW_FILE_MOVE} (id, {new}_id, from_path, to_path, created_at) "
        f"SELECT id, {old}_id, from_path, to_path, created_at FROM file_move"
    )
    op.drop_table("file_move")
    op.drop_table(f"{old}_tag")
    op.drop_table(old)
    op.rename_table(NEW_FILE_MOVE, "file_move")


def _create_tables(name: str) -> None:
    """The media table (as of 0006), its tag links (as of 0005) and file_move, under `name`."""
    source_check, kind_check = CHECK_NAMES[name]
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("taken_at_source", sa.String(), nullable=False),
        sa.Column("taken_at_utc_offset_minutes", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("imported_by_user_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("media_kind", sa.String(), nullable=False, server_default="image"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("video_codec", sa.String(), nullable=True),
        sa.Column("audio_codec", sa.String(), nullable=True),
        sa.Column("bitrate", sa.Integer(), nullable=True),
        sa.Column("frame_rate", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "taken_at_source IN ('exif', 'file_mtime', 'import_time', 'container')", name=op.f(source_check)
        ),
        sa.CheckConstraint("media_kind IN ('image', 'video')", name=op.f(kind_check)),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"], ["user.id"], name=f"fk_{name}_imported_by_user_id_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{name}"),
        sa.UniqueConstraint("sha256", name=f"uq_{name}_sha256"),
        sa.UniqueConstraint("storage_path", name=f"uq_{name}_storage_path"),
    )
    op.create_index(f"ix_{name}_taken_at_id", name, ["taken_at", "id"])
    op.create_index(f"ix_{name}_imported_at_id", name, ["imported_at", "id"])

    links = f"{name}_tag"
    op.create_table(
        links,
        sa.Column(f"{name}_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            [f"{name}_id"], [f"{name}.id"], name=f"fk_{links}_{name}_id_{name}", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], name=f"fk_{links}_tag_id_tag"),
        sa.PrimaryKeyConstraint(f"{name}_id", "tag_id", name=f"pk_{links}"),
    )
    op.create_index(f"ix_{links}_tag_id_{name}_id", links, ["tag_id", f"{name}_id"])
    op.create_index(f"ix_{links}_tag_id_added_at_{name}_id", links, ["tag_id", "added_at", f"{name}_id"])

    # Renamed to file_move once the old one is gone; its constraints already carry that name.
    op.create_table(
        NEW_FILE_MOVE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(f"{name}_id", sa.Integer(), nullable=False),
        sa.Column("from_path", sa.String(), nullable=False),
        sa.Column("to_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            [f"{name}_id"], [f"{name}.id"], name=f"fk_file_move_{name}_id_{name}", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_file_move"),
    )
