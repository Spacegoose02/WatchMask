"""BingeBandit HTTP API client and library sync.

https://github.com/Spacegoose02/BingeBandit/blob/main/docs/API.md
"""

import csv
import io

import requests
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from integrations.imports import helpers
from integrations.models import BingeBanditLibraryItem, BingeBanditResumePoint

LIBRARY_PAGE_SIZE = 500

MEDIA_TYPE_BY_KIND = {
    "movie": MediaTypes.MOVIE.value,
    "show": MediaTypes.TV.value,
}

LIBRARY_CACHE_KEY = "bingebandit_library:{user_id}"
LIBRARY_CACHE_SECONDS = 60 * 60 * 24

CONTINUE_WATCHING_LIMIT = 14


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
        try:
            response = requests.get(
                f"{self.base_url}/api{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            msg = f"Could not reach BingeBandit: {exc}"
            raise BingeBanditError(msg) from exc
        if response.status_code in (401, 403):
            msg = "BingeBandit API key is invalid or was revoked."
            raise BingeBanditAuthError(msg)
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
    cache.delete(LIBRARY_CACHE_KEY.format(user_id=account.user_id))

    account.library_synced_at = timezone.now()
    account.save(update_fields=["library_synced_at"])
    return len(item_ids)


def library_names(user, media_type):
    """Return the BingeBandit library names holding titles of a media type."""
    return list(
        BingeBanditLibraryItem.objects.filter(user=user, media_type=media_type)
        .exclude(library_name="")
        .values_list("library_name", flat=True)
        .distinct()
        .order_by("library_name"),
    )


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
