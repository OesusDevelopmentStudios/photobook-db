# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The first schema: images and their tags, users and their logins, settings, file moves and jobs."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, single_value): a single-value namespace holds at most one tag per image.
BUILTIN_NAMESPACES = [
    ("category", True),
    ("author", False),
    ("character", False),
    ("rating", True),
    ("album", False),
    ("general", False),
]


def upgrade() -> None:
    namespace_table = op.create_table(
        "namespace",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("single_value", sa.Boolean(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_namespace"),
        sa.UniqueConstraint("name", name="uq_namespace_name"),
    )
    op.create_table(
        "tag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], name="fk_tag_namespace_id_namespace"),
        sa.PrimaryKeyConstraint("id", name="pk_tag"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_tag_namespace_id_name"),
    )
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_user_role"),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
        sa.UniqueConstraint("username", name="uq_user_username"),
    )
    op.create_index("ix_user_role", "user", ["role"], unique=True, sqlite_where=sa.text("role = 'admin'"))
    op.create_table(
        "image",
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
        sa.CheckConstraint(
            "taken_at_source IN ('exif', 'file_mtime', 'import_time')", name="ck_image_taken_at_source"
        ),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"], ["user.id"], name="fk_image_imported_by_user_id_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_image"),
        sa.UniqueConstraint("sha256", name="uq_image_sha256"),
        sa.UniqueConstraint("storage_path", name="uq_image_storage_path"),
    )
    op.create_index("ix_image_taken_at_id", "image", ["taken_at", "id"])
    op.create_index("ix_image_imported_at_id", "image", ["imported_at", "id"])
    op.create_table(
        "image_tag",
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["image_id"], ["image.id"], name="fk_image_tag_image_id_image", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"], name="fk_image_tag_tag_id_tag"),
        sa.PrimaryKeyConstraint("image_id", "tag_id", name="pk_image_tag"),
    )
    op.create_index("ix_image_tag_tag_id_image_id", "image_tag", ["tag_id", "image_id"])
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_api_token_user_id_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_api_token"),
        sa.UniqueConstraint("token_sha256", name="uq_api_token_token_sha256"),
    )
    op.create_table(
        "setting",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_setting"),
    )
    op.create_table(
        "file_move",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("from_path", sa.String(), nullable=False),
        sa.Column("to_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["image_id"], ["image.id"], name="fk_file_move_image_id_image", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_file_move"),
    )
    op.create_table(
        "user_pref",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("hidden_filters", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_user_pref_user_id_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_pref"),
    )
    op.create_table(
        "user_restriction",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_user_restriction_user_id_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "term", name="pk_user_restriction"),
    )
    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("progress_done", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="ck_job_status"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_job_created_by_user", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_job"),
    )
    op.create_index("ix_job_status_id", "job", ["status", "id"])

    created_at = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        namespace_table,
        [
            {"name": name, "single_value": single_value, "is_builtin": True, "created_at": created_at}
            for name, single_value in BUILTIN_NAMESPACES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_job_status_id", table_name="job")
    op.drop_table("job")
    op.drop_table("user_restriction")
    op.drop_table("user_pref")
    op.drop_table("file_move")
    op.drop_table("setting")
    op.drop_table("api_token")
    op.drop_index("ix_image_tag_tag_id_image_id", table_name="image_tag")
    op.drop_table("image_tag")
    op.drop_index("ix_image_imported_at_id", table_name="image")
    op.drop_index("ix_image_taken_at_id", table_name="image")
    op.drop_table("image")
    op.drop_index("ix_user_role", table_name="user")
    op.drop_table("user")
    op.drop_table("tag")
    op.drop_table("namespace")
