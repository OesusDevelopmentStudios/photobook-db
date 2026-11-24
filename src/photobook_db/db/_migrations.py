# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

MIGRATIONS_FOLDER = Path(__file__).parent / "migrations"


def upgrade_to_latest_schema(engine: Engine) -> None:
    migrate(engine, "head")


def migrate(engine: Engine, revision: str, downgrade: bool = False) -> None:
    """To a revision, up or down. SQLite changes most of a table's definition only by copying the table, and with
    foreign keys on, dropping the old copy would delete the rows that refer to it. So, as SQLite's own procedure
    for such changes asks, they are off during the migration and checked before it is kept."""
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction: first
        connection.commit()
        try:
            with connection.begin():
                (command.downgrade if downgrade else command.upgrade)(_alembic_config(connection), revision)
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"The migration left rows that refer to missing ones: {broken[:5]}")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")  # as every connection of the server has them
            connection.commit()


def _alembic_config(connection: object) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_FOLDER))
    config.attributes["connection"] = connection  # read by migrations/env.py
    return config
