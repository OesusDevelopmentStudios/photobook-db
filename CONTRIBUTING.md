# Contributing to photobook-db

Readability comes first. The code should be easy to understand and maintain for someone who has
never seen it before.

## Building and runnin the server

Set up a virtual environment in the repository and install the project with its development tools:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```
Run the server (in the virtual environment):

```sh
photobook-db serve
curl http://127.0.0.1:8737/api/v1/server/info
```

## Avialble settings

These environment variables are read when the server starts.

| Environment variable | Default | Meaning |
|---|---|---|
| `PHOTOBOOK_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` makes the server reachable from other computers |
| `PHOTOBOOK_PORT` | `8737` | Port to listen on |
| `PHOTOBOOK_DATA_DIR` | `~/.local/share/photobook-db` | Folder for the database. Created if missing |
| `PHOTOBOOK_LIBRARY_DIR` | `~/Pictures/photobook-library` | Folder for the images, arranged by their tags. Created if missing |
| `PHOTOBOOK_IMPORT_DIR` | not set | Folder that admins can import from with an import job, e.g. a NAS share. Unset: no folder imports |
| `PHOTOBOOK_TRUSTED_PROXIES` | `127.0.0.1` | Addresses (or networks, comma-separated, or `*`) of a reverse proxy whose `X-Forwarded-*` headers are believed. See [deploy/Caddyfile.example](deploy/Caddyfile.example) |
| `PHOTOBOOK_HOST_MACHINE_ID_FILE` | `/etc/machine-id` | The machine ID of the computer the server runs on. A desktop app that proves it runs there gets the library folder's path, to open an image's folder in the file manager. The container image reads `/run/host/machine-id`: mount the host's `/etc/machine-id` there, read-only, as [deploy/photobook-db.container](deploy/photobook-db.container) does |
| `PHOTOBOOK_HOST_LIBRARY_DIR` | `PHOTOBOOK_LIBRARY_DIR` | The library folder as programs on that computer see it; in a container, the host folder mounted at `/library` |

## API documentation

The interactive API documentation is at <http://127.0.0.1:PHOTOBOOK_PORT/api/docs>.

## Workflow

- **Small commits.** Each commit does one thing, passes the tests and can be reviewed on its own.
- **Tests come with the code.** New logic comes with a test in `tests/`.
- Before committing:
  ```sh
  pytest
  flake8
  shellcheck deploy/install.sh    # after changing it
  ```

## Code layout

```
src/photobook_db/
├── app.py          creates the services and the web application; contains no logic of its own
├── config.py       settings read from environment variables at startup
├── db/             database tables (models.py), connection and migrations
├── api/v1/         REST API: one router per resource
├── admin_web/      web admin pages
├── services/       one package per service, each with a single responsibility
└── utils/          small helpers shared by several services
tests/              mirrors src/photobook_db/: tests/api/v1/test_server.py tests api/v1/server.py
```

**Routers and admin pages are thin.** They validate the input, call a service and turn the result
into a response. Decisions live in the services, where they can be unit-tested, and the REST API
and the admin pages use the same services.

### Service layout

```
services/<service_name>/       snake_case, e.g. services/tags/
├── __init__.py                public interface: the only module other code may import
└── _<module>.py               internals, e.g. _normalise.py
```

- Services never create each other. `app.py` creates them and passes in their dependencies, so
  tests can pass in fakes.

## Database changes

The schema changes only through Alembic migrations in `src/photobook_db/db/migrations/versions/`,
one migration per reviewable change. The server applies them itself when it starts.

1. Change the tables in `src/photobook_db/db/models.py`.
2. Let Alembic write the migration. It compares the models with `migration-dev.sqlite3` in the
   repository root (ignored by git), so bring that file up to date first. Use the next number as id:
   ```sh
   alembic upgrade head
   alembic revision --autogenerate --rev-id 0002 -m "short description"
   ```
3. Read the generated file and tidy it up; it is code like any other.
4. `pytest` fails if the models and the migrations do not describe the same schema.

## API changes

The API schema is committed as [docs/openapi.json](docs/openapi.json), and a test checks that it matches the code. After an intended API change, update it with:

```sh
photobook-db openapi > docs/openapi.json
```

## Python style

Formatting follows PEP 8, checked by flake8 with a maximum line length of 120 columns.

| What | Style | Example |
|---|---|---|
| Modules, packages, functions, variables | `snake_case` | `server_info()` |
| Classes | `PascalCase` | `ServerInfo` |
| Constants | `UPPER_SNAKE_CASE` | `API_VERSION` |
| Internal modules and names | leading `_` | `_normalise.py` |

- **Type hints everywhere.** Every function has type hints for its parameters and its return type.
- **Names are verbose.** No single-letter names, not even for loop counters: `image_index`, not `i`.
- **Each function has a single responsibility.**
- **Comments are short** and only where they add something the code does not already say.

## License headers

Every source file starts with an SPDX header:

```python
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios
```
