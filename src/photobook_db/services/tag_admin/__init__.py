# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Renaming, merging and deleting tags across the library, for admins. Restrictions and hidden filters follow:
they name the new tag after a rename, and lose a deleted one."""

from collections.abc import Callable

from photobook_db.services.access import AccessService
from photobook_db.services.images import ImagesService
from photobook_db.services.jobs import JobInfo, Relayout
from photobook_db.services.preferences import PreferencesService
from photobook_db.services.search import renamed_in_term, without_namespace_in_term, without_tag_in_term
from photobook_db.services.storage import StorageService
from photobook_db.services.tags import TagName, TagsService
from photobook_db.utils.problems import Problem


class TagAdmin:
    def __init__(
        self,
        tags: TagsService,
        images: ImagesService,
        access: AccessService,
        preferences: PreferencesService,
        storage: StorageService,
        relayout: Relayout,
    ) -> None:
        self._tags = tags
        self._images = images
        self._access = access
        self._preferences = preferences
        self._storage = storage
        self._relayout = relayout

    def rename_tag(self, old_tag: TagName, new_tag_text: str, user_id: int | None) -> tuple[TagName, JobInfo | None]:
        """Gives every image with old_tag the new tag instead; an existing new tag is merged with it. In a
        single-value namespace the new tag replaces the image's current one. Returns the new tag and the job
        that moves the affected files, if the path template uses either namespace. Raises 404 not_found,
        422 invalid_tag, or 422 namespace_read_only for the server's own tags (meta), either one."""
        self._check_admin_may_change(old_tag.namespace)
        self._check_admin_may_change(self._tags.parse_tag(new_tag_text).namespace)
        [new_tag] = self._tags.resolve_tags([new_tag_text])
        self._check_exists(old_tag)
        if new_tag == old_tag:
            return new_tag, None

        namespace_names = self._tags.namespace_names()
        self._rewrite_terms(lambda term: renamed_in_term(term, old_tag, new_tag, namespace_names))
        changed_ids = self._images.replace_tag(old_tag, new_tag)
        return new_tag, self._relayout_if_needed(changed_ids, {old_tag.namespace, new_tag.namespace}, user_id)

    def create_tag(self, tag_text: str) -> TagName:
        """A tag no image has yet, so that it is suggested to users. Raises 409 already_exists, 422 invalid_tag
        (also for a rating that is not allowed) or 422 namespace_read_only."""
        self._check_admin_may_change(self._tags.parse_tag(tag_text).namespace)
        [tag] = self._tags.resolve_tags([tag_text])
        self._tags.create_tag(tag)
        return tag

    def delete_tag(self, tag: TagName, user_id: int | None) -> JobInfo | None:
        """Removes the tag from every image and deletes it. Returns the job that moves the affected files, if
        the path template uses its namespace. Raises 404 not_found or 422 namespace_read_only."""
        self._check_admin_may_change(tag.namespace)
        self._check_exists(tag)
        namespace_names = self._tags.namespace_names()
        self._rewrite_terms(lambda term: without_tag_in_term(term, tag, namespace_names))
        changed_ids = self._images.replace_tag(tag, None)
        return self._relayout_if_needed(changed_ids, {tag.namespace}, user_id)

    def delete_namespace(self, name: str) -> None:
        """A custom namespace and its tags: they come off every image and are deleted, and restrictions and hidden
        filters lose the terms that name them. Raises 404 not_found, or 409 namespace_in_use for a built-in
        namespace or one the path template uses (so no file moves)."""
        self._tags.check_namespace_removable(name)
        namespace_names = self._tags.namespace_names()
        self._rewrite_terms(lambda term: without_namespace_in_term(term, name, namespace_names))
        self._images.delete_namespace_tags(name)
        self._tags.delete_namespace(name)

    def _check_admin_may_change(self, namespace: str) -> None:
        if namespace in self._tags.read_only_namespace_names():
            raise Problem(
                422,
                "namespace_read_only",
                "Tags set by the server",
                f"The server sets the {namespace} tags from the files; they cannot be renamed, merged or deleted.",
            )

    def _check_exists(self, tag: TagName) -> None:
        if not self._tags.tag_exists(tag):
            raise Problem(404, "not_found", "Tag not found")

    def _rewrite_terms(self, rewrite: Callable[[str], str | None]) -> None:
        """Before the images change: after a crash in between, the tag still exists and the change can be
        repeated."""
        self._access.rewrite_restricted_terms(rewrite)
        self._preferences.rewrite_hidden_filters(rewrite)

    def _relayout_if_needed(self, changed_ids: list[int], namespaces: set[str], user_id: int | None) -> JobInfo | None:
        if not changed_ids or not namespaces & self._storage.path_template_namespaces():
            return None

        return self._relayout.start(user_id, changed_ids)


__all__ = ["TagAdmin"]
