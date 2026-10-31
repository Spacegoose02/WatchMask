"""BingeBandit HTTP API client and library sync.

https://github.com/Spacegoose02/BingeBandit/blob/main/docs/API.md
"""

import csv
import hashlib
import hmac
import io
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from integrations.imports import helpers
from integrations.models import (
    BingeBanditLibraryItem,
    BingeBanditResumePoint,
    CollectionSourceState,
)

logger = logging.getLogger(__name__)

LIBRARY_PAGE_SIZE = 500

MEDIA_TYPE_BY_KIND = {
    "movie": MediaTypes.MOVIE.value,
    "show": MediaTypes.TV.value,
}

LIBRARY_CACHE_KEY = "bingebandit_library:{user_id}"
LIBRARY_CACHE_SECONDS = 60 * 60 * 24
LIBRARY_NAMES_CACHE_KEY = "bingebandit_library_names:{user_id}"

CONTINUE_WATCHING_LIMIT = 14

KIND_BY_MEDIA_TYPE = {value: key for key, value in MEDIA_TYPE_BY_KIND.items()}

COLLECTION_SOURCE = "bingebandit"

# New titles looked up on TMDB per library sync. The rest are picked up by the
# next sync, so a first sync of a very large server can't run for hours.
COLLECTION_NEW_ITEMS_PER_SYNC = 500


class BingeBanditError(Exception):
    """BingeBandit request failed."""


class BingeBanditAuthError(BingeBanditError):
    """BingeBandit rejected the API key."""


class BingeBanditClient:
    """Talk to a BingeBandit server with a user's API key."""

    def __init__(self, base_url: str, api_key: str):
        """Initialize with the server URL and a `bb_` API key."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _get(self, path, params=None, timeout=30):
        return self._send("GET", path, params=params, timeout=timeout)

    def _send(self, method, path, params=None, json=None, timeout=30):
        try:
            response = requests.request(
                method,
                f"{self.base_url}/api{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params=params,
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            msg = f"Could not reach BingeBandit: {exc}"
            raise BingeBanditError(msg) from exc
        if response.status_code in (401, 403):
            msg = "BingeBandit API key is invalid or was revoked."
            raise BingeBanditAuthError(msg)
        if response.status_code == 409:  # noqa: PLR2004
            msg = "That title is already on the BingeBandit server."
            raise BingeBanditError(msg)
        if not response.ok:
            msg = f"BingeBandit request failed ({response.status_code}) for {path}"
            raise BingeBanditError(msg)
        return response

    def get_me(self):
        """Return the user the API key belongs to."""
        return self._get("/me").json()

    def get_libraries(self, timeout=30):
        """Return the libraries the user can see as [{id, name, kind}]."""
        return [
            {"id": lib["id"], "name": lib.get("name", ""), "kind": lib.get("kind", "")}
            for lib in self._get("/libraries", timeout=timeout).json()
        ]

    def get_library_items(self, library_id):
        """Return the TMDB-matched movies and shows in a library."""
        matched = []
        offset = 0
        while True:
            page = self._get(
                f"/libraries/{library_id}/items",
                {"offset": offset, "limit": LIBRARY_PAGE_SIZE},
            ).json()
            items = page.get("items") or []
            matched.extend(
                item
                for item in items
                if item.get("kind") in MEDIA_TYPE_BY_KIND
                and item.get("tmdbId")
                and item.get("id")
            )
            offset += len(items)
            if not items or offset >= page.get("total", 0):
                return matched

    def get_library_tmdb_ids(self, library_id):
        """Return the TMDB IDs of the movies and shows in a library."""
        return {str(item["tmdbId"]) for item in self.get_library_items(library_id)}

    def mark_watched(self, item_id, watched=True):
        """Mark a movie, or every episode of a show, watched on the server."""
        self._send("POST" if watched else "DELETE", f"/items/{int(item_id)}/watched")

    def request_title(self, kind, tmdb_id, title, year=None, poster="", overview=""):
        """Ask the server's admin to add a movie or show. Returns the request."""
        try:
            response = self._send(
                "POST",
                "/requests",
                json={
                    "kind": kind,
                    "tmdbId": int(tmdb_id),
                    "title": title,
                    "year": int(year or 0),
                    "poster": poster or "",
                    "overview": (overview or "")[:2000],
                },
            )
        except BingeBanditAuthError as exc:
            # 403 here means requests are turned off for this user, not a bad key.
            msg = "BingeBandit doesn't allow requests from this account."
            raise BingeBanditError(msg) from exc
        return response.json() if response.content else {}

    def get_history(self):
        """Return the user's plays from the history CSV export, newest first.

        Unlike /me/history, the export carries TMDB IDs (the show's for
        episodes) and season/episode numbers.
        """
        response = self._get("/me/export/history.csv")
        response.encoding = "utf-8"
        return list(csv.DictReader(io.StringIO(response.text)))


def client_for(account):
    """Build a client from a stored account."""
    return BingeBanditClient(account.base_url, helpers.decrypt(account.api_key))


def sync_library(account):
    """Refresh which movies and shows the user can play. Returns the count."""
    client = client_for(account)
    item_ids = {}
    library_rows = {}
    for library in client.get_libraries():
        library_name = library["name"] or str(library["id"])
        for item in client.get_library_items(library["id"]):
            key = (MEDIA_TYPE_BY_KIND[item["kind"]], str(item["tmdbId"]))
            item_ids.setdefault(key, int(item["id"]))
            library_rows.setdefault((*key, library_name), int(item["id"]))

    with transaction.atomic():
        BingeBanditLibraryItem.objects.filter(user=account.user).delete()
        BingeBanditLibraryItem.objects.bulk_create(
            (
                BingeBanditLibraryItem(
                    user=account.user,
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    library_name=library_name,
                    item_id=item_id,
                )
                for (media_type, tmdb_id, library_name), item_id in library_rows.items()
            ),
            batch_size=1000,
        )
    cache.delete_many(
        [
            LIBRARY_CACHE_KEY.format(user_id=account.user_id),
            LIBRARY_NAMES_CACHE_KEY.format(user_id=account.user_id),
        ],
    )

    if account.sync_collection:
        sync_collection(account.user, set(item_ids))

    account.library_synced_at = timezone.now()
    account.save(update_fields=["library_synced_at"])
    return len(item_ids)


def _resolve_items(keys, limit):
    """Map (media_type, tmdb_id) keys to Items, creating up to ``limit`` new ones."""
    from app.providers import services

    items = {}
    for media_type in {media_type for media_type, _ in keys}:
        tmdb_ids = [tmdb_id for kind, tmdb_id in keys if kind == media_type]
        for start in range(0, len(tmdb_ids), 500):
            for item in Item.objects.filter(
                source=Sources.TMDB.value,
                media_type=media_type,
                media_id__in=tmdb_ids[start : start + 500],
            ):
                items[(media_type, item.media_id)] = item

    created = 0
    for media_type, tmdb_id in sorted(keys - items.keys()):
        if created >= limit:
            break
        created += 1
        try:
            metadata = services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
            )
        except Exception as exc:
            logger.info(
                "Skipping BingeBandit title %s %s: %s",
                media_type,
                tmdb_id,
                exc,
            )
            continue
        title = metadata.get("title") or tmdb_id
        items[(media_type, tmdb_id)], _ = Item.objects.get_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=media_type,
            defaults={
                "title": title,
                "image": metadata.get("image") or settings.IMG_NONE,
                "release_datetime": metadata.get("release_datetime"),
                "genres": metadata.get("genres") or [],
                "original_title": metadata.get("original_title") or title,
                "localized_title": metadata.get("localized_title") or title,
            },
        )
    return items


def sync_collection(user, keys, new_items_limit=COLLECTION_NEW_ITEMS_PER_SYNC):
    """Mark the server's movies and shows collected, and un-collect removed ones.

    ``keys`` is the full set of (media_type, tmdb_id) on the server. Returns
    (added, removed).
    """
    from app.signals import suppress_media_change_side_effects
    from integrations.source_sync import (
        prune_collection_source_states,
        upsert_collection_source_state,
    )
    from integrations.tasks._plex_collection import (
        _replay_collection_side_effects,
    )

    items = _resolve_items(keys, new_items_limit)
    keep_ids = {item.id for item in items.values()}
    already = set(
        CollectionSourceState.objects.filter(
            user=user,
            source=COLLECTION_SOURCE,
        ).values_list("item_id", flat=True),
    )
    added = 0
    with suppress_media_change_side_effects():
        for item in items.values():
            if item.id in already:
                continue
            upsert_collection_source_state(
                user=user,
                item=item,
                source=COLLECTION_SOURCE,
            )
            added += 1
        removed = prune_collection_source_states(
            user=user,
            source=COLLECTION_SOURCE,
            source_instance_id=None,
            keep_item_ids=keep_ids,
        )
    if added or removed:
        _replay_collection_side_effects(user.id)
    return added, removed


def verify_signature(secret, body, header):
    """Check a webhook's X-BingeBandit-Signature (``sha256=<hex hmac>``)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def push_watched(user, media_type, tmdb_id):
    """Mark a title watched on the user's BingeBandit server, if it's there."""
    account = getattr(user, "bingebandit_account", None)
    if not account or not account.push_watched:
        return False
    item_id = library_item_ids(user).get((media_type, str(tmdb_id)))
    if not item_id:
        return False
    client_for(account).mark_watched(item_id)
    return True


def library_names(user, media_type):
    """Return the BingeBandit library names holding titles of a media type.

    Cached until the next library sync: the Movies and TV lists ask on every
    page load.
    """
    key = LIBRARY_NAMES_CACHE_KEY.format(user_id=user.id)
    names = cache.get(key)
    if names is None:
        names = {}
        for kind, name in (
            BingeBanditLibraryItem.objects.filter(user=user)
            .exclude(library_name="")
            .values_list("media_type", "library_name")
            .distinct()
        ):
            names.setdefault(kind, set()).add(name)
        names = {kind: sorted(values) for kind, values in names.items()}
        cache.set(key, names, LIBRARY_CACHE_SECONDS)
    return list(names.get(media_type, []))


def library_tmdb_ids(user, media_type, library_name):
    """Return a subquery of the TMDB IDs in one of the user's BingeBandit libraries."""
    return BingeBanditLibraryItem.objects.filter(
        user=user,
        media_type=media_type,
        library_name=library_name,
    ).values("tmdb_id")


def library_item_ids(user):
    """Map (media_type, tmdb_id) to BingeBandit item IDs for a user, cached."""
    key = LIBRARY_CACHE_KEY.format(user_id=user.id)
    item_ids = cache.get(key)
    if item_ids is None:
        item_ids = {
            (media_type, tmdb_id): item_id
            for media_type, tmdb_id, item_id in BingeBanditLibraryItem.objects.filter(
                user=user,
            ).values_list("media_type", "tmdb_id", "item_id")
        }
        cache.set(key, item_ids, LIBRARY_CACHE_SECONDS)
    return item_ids


def continue_watching(user):
    """Resume points for the Home page, newest first, with posters and links."""
    account = getattr(user, "bingebandit_account", None)
    if not account:
        return []
    points = list(
        BingeBanditResumePoint.objects.filter(user=user)[:CONTINUE_WATCHING_LIMIT],
    )
    if not points:
        return []
    items = {
        (item.media_type, item.media_id): item
        for item in Item.objects.filter(
            source=Sources.TMDB.value,
            media_type__in=MEDIA_TYPE_BY_KIND.values(),
            media_id__in={point.tmdb_id for point in points},
        )
    }
    return [
        {
            "point": point,
            "item": items.get((point.media_type, point.tmdb_id)),
            "resume_url": account.play_url(point.item_id, point.position_seconds),
        }
        for point in points
    ]
