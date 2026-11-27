# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import base64
import binascii
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass

from sqlalchemy import and_, exists, func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError

from photobook_db.db import Database
from photobook_db.db.models import Namespace, Tag, media_tag
from photobook_db.services.tags._names import (
    TagName,
    normalized_name_prefix,
    normalized_namespace_name,
    normalized_tag_name,
    split_tag,
)
from photobook_db.utils.problems import Problem
from photobook_db.utils.time import utc_now


@dataclass(frozen=True)
class NamespaceInfo:
    name: str
    single_value: bool
    is_builtin: bool
    in_path_template: bool  # its tags form folder names in the library
    user_editable: bool = True  # false: the server alone sets its tags, e.g. "meta"
    allowed_values: tuple[str, ...] | None = None  # a namespace with fixed values: its tags, e.g. the ratings


@dataclass(frozen=True)
class TagPage:
    items: list["TagCount"]
    next_cursor: str | None  # None on the last page


@dataclass(frozen=True)
class TagCount:
    tag: TagName
    media_count: int


META_NAMESPACE = "meta"

# Sorts after every other character, so names from `prefix` up to `prefix + this` start with `prefix`.
_LAST_CHARACTER = "\U0010ffff"


class TagsService:
    """Namespaces and tags. `path_template_namespaces` returns the namespaces the library's path template
    uses; they cannot be deleted."""

    def __init__(self, database: Database, path_template_namespaces: Callable[[], frozenset[str]]) -> None:
        self._database = database
        self._path_template_namespaces = path_template_namespaces

    def resolve_tags(self, texts: list[str], new_values_allowed: bool = False) -> list[TagName]:
        """Parses tags as given for a media item: each tag once, and in a single-value namespace only the last
        one, because a later tag replaces an earlier one (category:photo, category:artwork → artwork).
        In a namespace with fixed values (ratings) a tag must exist, unless an admin adds a value
        (`new_values_allowed`): a typo must not slip past a restriction such as rating:private."""
        tags = [self.parse_tag(text) for text in texts]
        self.check_user_editable(tags)
        if not new_values_allowed:
            self._check_fixed_values(tags, texts)

        single_value = {namespace.name for namespace in self.list_namespaces() if namespace.single_value}
        kept: list[TagName] = []
        seen_single_value: set[str] = set()
        for tag in reversed(list(dict.fromkeys(tags))):
            if tag.namespace in single_value:
                if tag.namespace in seen_single_value:
                    continue

                seen_single_value.add(tag.namespace)

            kept.append(tag)

        return kept[::-1]

    def check_user_editable(self, tags: Collection[TagName]) -> None:
        """Raises 422 tag_read_only for tags of a namespace that only the server tags with, e.g. meta."""
        read_only = self.read_only_namespace_names()
        refused = [str(tag) for tag in tags if tag.namespace in read_only]
        if refused:
            raise Problem(
                422,
                "tag_read_only",
                "Tag set by the server",
                "The server sets these tags from the file itself; they cannot be added or removed.",
                tags=refused,
            )

    def fixed_values(self) -> dict[str, list[str]]:
        """The namespaces with fixed values and their values (their tags), in the order they were added."""
        query = (
            select(Namespace.name, Tag.name)
            .join(Tag, Tag.namespace_id == Namespace.id)
            .where(Namespace.fixed_values.is_(True))
            .order_by(Tag.id)
        )
        values: dict[str, list[str]] = {}
        with self._database.session() as session:
            for name in session.scalars(select(Namespace.name).where(Namespace.fixed_values.is_(True))):
                values[name] = []

            for namespace_name, tag_name in session.execute(query):
                values[namespace_name].append(tag_name)

        return values

    def parse_tag(self, text: str) -> TagName:
        """"Author:Sample Artist" → author:sample_artist. Raises 422 invalid_tag."""
        namespace, name = split_tag(text, self.namespace_names())
        return TagName(namespace, normalized_tag_name(name))

    def tag_exists(self, tag: TagName) -> bool:
        query = (
            select(Tag.id)
            .join(Namespace, Tag.namespace_id == Namespace.id)
            .where(Namespace.name == tag.namespace, Tag.name == tag.name)
        )
        with self._database.session() as session:
            return session.scalar(query) is not None

    def media_count(self, tag: TagName) -> int | None:
        """How many media have the tag, counting all of them; None if the tag does not exist."""
        query = (
            select(func.count(media_tag.c.media_id))
            .select_from(Tag)
            .join(Namespace, Tag.namespace_id == Namespace.id)
            .outerjoin(media_tag, media_tag.c.tag_id == Tag.id)
            .where(Namespace.name == tag.namespace, Tag.name == tag.name)
            .group_by(Tag.id)
        )
        with self._database.session() as session:
            return session.scalar(query)

    def media_counts(self, tags: list[TagName]) -> list[TagCount]:
        """How many media have each tag, counting all of them, in the order given. In one query: the viewer asks
        for every tag of the media item on screen."""
        names_by_namespace: dict[str, list[str]] = {}
        for tag in tags:
            names_by_namespace.setdefault(tag.namespace, []).append(tag.name)

        if not names_by_namespace:
            return []

        wanted = or_(*(and_(Namespace.name == ns, Tag.name.in_(names)) for ns, names in names_by_namespace.items()))
        query = (
            select(Namespace.name, Tag.name, func.count(media_tag.c.media_id))
            .select_from(Tag)
            .join(Namespace, Tag.namespace_id == Namespace.id)
            .outerjoin(media_tag, media_tag.c.tag_id == Tag.id)
            .where(wanted)
            .group_by(Tag.id)
        )
        with self._database.session() as session:
            counts = {TagName(namespace, name): count for namespace, name, count in session.execute(query)}

        return [TagCount(tag, counts.get(tag, 0)) for tag in tags]

    def autocomplete(
        self,
        prefix: str = "",
        namespace: str | None = None,
        limit: int = 20,
        is_hidden: Callable[[TagName], bool] | None = None,
    ) -> list[TagCount]:
        """Tags starting with `prefix`, most used first. "author:sam" searches one namespace, "sam" all of them.
        Tags for which `is_hidden` says so are left out (a restricted user's tags). `media_count` counts all
        media, also ones a restricted user cannot see."""
        return self._most_used(prefix, namespace, limit, is_hidden, after=None)

    def tag_page(
        self,
        prefix: str = "",
        namespace: str | None = None,
        limit: int = 20,
        is_hidden: Callable[[TagName], bool] | None = None,
        cursor: str | None = None,
    ) -> TagPage:
        """As autocomplete(), a page at a time, for browsing every tag of a namespace. Media tagged meanwhile
        change the counts, so a tag may be skipped or repeated across pages. Raises 422 validation_error for a
        damaged cursor."""
        after = None if cursor is None else _decode_cursor(cursor)
        found = self._most_used(prefix, namespace, limit + 1, is_hidden, after)
        items = found[:limit]
        return TagPage(items, _encode_cursor(items[-1]) if len(found) > limit else None)

    def create_tag(self, tag: TagName) -> None:
        """A tag that no media item has yet. Raises 409 already_exists."""
        with self._database.session() as session:
            namespace_id = session.scalar(select(Namespace.id).where(Namespace.name == tag.namespace))
            session.add(Tag(namespace_id=namespace_id, name=tag.name))
            try:
                session.commit()
            except IntegrityError:
                raise Problem(409, "already_exists", "Tag exists", f"The tag {tag} exists already.") from None

    def _most_used(
        self,
        prefix: str,
        namespace: str | None,
        limit: int,
        is_hidden: Callable[[TagName], bool] | None,
        after: TagCount | None,
    ) -> list[TagCount]:
        """Most used first, then by namespace and name; with `after`, the tags that come after that one."""
        prefix_namespace, name_prefix = self._split_prefix(prefix)
        namespaces = {name for name in (prefix_namespace, namespace and namespace.lower()) if name}
        if len(namespaces) > 1:
            return []  # the namespace filter and the prefix's namespace disagree

        media_count = func.count(media_tag.c.media_id)
        query = (
            select(Namespace.name, Tag.name, media_count)
            .join(Namespace, Tag.namespace_id == Namespace.id)
            .outerjoin(media_tag, media_tag.c.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(media_count.desc(), Namespace.name, Tag.name)
        )
        if namespaces:
            query = query.where(Namespace.name == namespaces.pop())

        if name_prefix:
            query = query.where(Tag.name >= name_prefix, Tag.name < name_prefix + _LAST_CHARACTER)

        if after is not None:
            same_count_later = and_(
                media_count == after.media_count,
                tuple_(Namespace.name, Tag.name) > tuple_(after.tag.namespace, after.tag.name),
            )
            query = query.having(or_(media_count < after.media_count, same_count_later))

        # Hidden tags are dropped after reading, so more rows are read until the page is full.
        found: list[TagCount] = []
        batch_size = limit if is_hidden is None else limit * 4
        offset = 0
        with self._database.session() as session:
            while len(found) < limit:
                rows = session.execute(query.limit(batch_size).offset(offset)).all()
                for namespace_name, tag_name, count in rows:
                    tag = TagName(namespace_name, tag_name)
                    if is_hidden is None or not is_hidden(tag):
                        found.append(TagCount(tag, count))

                if len(rows) < batch_size:
                    break

                offset += batch_size

        return found[:limit]

    def _check_fixed_values(self, tags: list[TagName], texts: list[str]) -> None:
        fixed_values = self.fixed_values()
        for tag, text in zip(tags, texts):
            allowed = fixed_values.get(tag.namespace)
            if allowed is not None and tag.name not in allowed:
                raise Problem(
                    422,
                    "invalid_tag",
                    "Invalid tag",
                    f"{tag.namespace} is one of: {', '.join(allowed)}. An admin can add more on the namespace's page.",
                    tag=text,
                )

    def _split_prefix(self, prefix: str) -> tuple[str | None, str]:
        """Like split_tag, but text without a namespace searches all namespaces instead of general only."""
        namespace, separator, rest = prefix.partition(":")
        if separator and namespace.strip().lower() in self.namespace_names():
            return namespace.strip().lower(), normalized_name_prefix(rest)

        return None, normalized_name_prefix(prefix)

    # --- Namespaces

    def namespace_names(self) -> set[str]:
        with self._database.session() as session:
            return set(session.scalars(select(Namespace.name)))

    def read_only_namespace_names(self) -> set[str]:
        """The namespaces whose tags only the server sets, e.g. meta."""
        with self._database.session() as session:
            return set(session.scalars(select(Namespace.name).where(Namespace.user_editable.is_(False))))

    def folder_namespace_names(self) -> set[str]:
        """The namespaces the path template may use: not the server's own, as a file always has several of those.
        Read here, not through list_namespaces(), which asks the path template."""
        with self._database.session() as session:
            return set(session.scalars(select(Namespace.name).where(Namespace.user_editable.is_(True))))

    def list_namespaces(self) -> list[NamespaceInfo]:
        """Built-in namespaces first, then the others in the order they were created."""
        path_namespaces = self._path_template_namespaces()
        fixed_values = self.fixed_values()
        with self._database.session() as session:
            namespaces = session.scalars(select(Namespace).order_by(Namespace.id))
            return [_namespace_info(namespace, path_namespaces, fixed_values) for namespace in namespaces]

    def create_namespace(self, name: str, single_value: bool) -> NamespaceInfo:
        name = normalized_namespace_name(name)
        namespace = Namespace(name=name, single_value=single_value, is_builtin=False, created_at=utc_now())
        with self._database.session() as session:
            session.add(namespace)
            try:
                session.commit()
            except IntegrityError:
                raise Problem(
                    409, "already_exists", "Namespace exists", f"A namespace {name!r} exists already."
                ) from None

            return _namespace_info(namespace, self._path_template_namespaces(), {})  # a new one has no fixed values

    def check_namespace_removable(self, name: str) -> None:
        """Custom namespaces that the path template does not use. Raises 404 not_found or 409 namespace_in_use."""
        with self._database.session() as session:
            namespace = session.scalars(select(Namespace).where(Namespace.name == name)).one_or_none()
            if namespace is None:
                raise Problem(404, "not_found", "Namespace not found")

            reason = self._reason_namespace_is_in_use(namespace)
            if reason is not None:
                raise Problem(409, "namespace_in_use", "Namespace is in use", reason)

    def namespace_usage(self, name: str) -> tuple[int, int]:
        """How many tags the namespace has, and how many media have one of them."""
        tag_ids = select(Tag.id).join(Namespace, Tag.namespace_id == Namespace.id).where(Namespace.name == name)
        media_count = select(func.count(media_tag.c.media_id.distinct())).where(media_tag.c.tag_id.in_(tag_ids))
        with self._database.session() as session:
            tag_count = session.scalar(select(func.count()).select_from(tag_ids.subquery())) or 0
            return tag_count, session.scalar(media_count) or 0

    def delete_namespace(self, name: str) -> None:
        """A removable namespace (check_namespace_removable()) without tags; the tag admin takes its tags first."""
        self.check_namespace_removable(name)
        with self._database.session() as session:
            namespace = session.scalars(select(Namespace).where(Namespace.name == name)).one()
            if session.scalar(select(exists().where(Tag.namespace_id == namespace.id))):
                raise Problem(409, "namespace_in_use", "Namespace is in use", "It still has tags.")

            session.delete(namespace)
            session.commit()

    def _reason_namespace_is_in_use(self, namespace: Namespace) -> str | None:
        if namespace.is_builtin:
            return "Built-in namespaces cannot be deleted."

        if namespace.name in self._path_template_namespaces():
            return "The library's path template uses this namespace."

        return None


def _namespace_info(
    namespace: Namespace, path_namespaces: frozenset[str], fixed_values: dict[str, list[str]]
) -> NamespaceInfo:
    allowed_values = fixed_values.get(namespace.name)
    return NamespaceInfo(
        name=namespace.name,
        single_value=namespace.single_value,
        is_builtin=namespace.is_builtin,
        in_path_template=namespace.name in path_namespaces,
        user_editable=namespace.user_editable,
        allowed_values=None if allowed_values is None else tuple(allowed_values),
    )


def _encode_cursor(last: TagCount) -> str:
    """base64url JSON {"count": <its media count>, "tag": "<namespace>:<name>"} of the page's last tag."""
    text = json.dumps({"count": last.media_count, "tag": str(last.tag)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> TagCount:
    try:
        content = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        count, text = content["count"], content["tag"]
        namespace, separator, name = text.partition(":")
        if not isinstance(count, int) or not separator:
            raise ValueError

        return TagCount(TagName(namespace, name), count)
    except (binascii.Error, ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
        raise Problem(
            422,
            "validation_error",
            "Invalid request",
            "The request body or parameters are invalid.",
            errors=[{"field": "query.cursor", "message": "Use next_cursor of the previous page."}],
        ) from None
