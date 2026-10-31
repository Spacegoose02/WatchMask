"""Cinemask API client and request sync.

Cinemask (https://github.com/Spacegoose02/Cinemask) is a request manager in
the style of Jellyseerr. Yamtrack sends requests for tracked movies and shows,
and keeps the last-known request status so it can be shown on detail pages.
"""

import logging

import requests
from django.utils import timezone

from app.models import MediaTypes, Status
from integrations.imports import helpers
from integrations.models import CinemaskRequest, CinemaskRequestStatus
from integrations.webhooks.seerr import SeerrWebhookProcessor

logger = logging.getLogger(__name__)

# Cinemask calls TV shows "show".
KIND_BY_MEDIA_TYPE = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "show",
}
MEDIA_TYPE_BY_KIND = {
    kind: media_type for media_type, kind in KIND_BY_MEDIA_TYPE.items()
}

# GET /api/requests returns at most this many rows.
REQUEST_LIST_LIMIT = 500


class CinemaskError(Exception):
    """Base Cinemask client error."""


class CinemaskAuthError(CinemaskError):
    """The API key was rejected."""


class CinemaskClient:
    """Thin client for the Cinemask JSON API."""

    def __init__(self, base_url: str, api_key: str):
        """Initialize with server base URL and API key."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @classmethod
    def for_account(cls, account):
        """Build a client from a stored account."""
        return cls(account.base_url, helpers.decrypt(account.api_key))

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={"X-Api-Key": self.api_key},
                timeout=20,
                **kwargs,
            )
        except requests.RequestException as exc:
            msg = f"Could not reach Cinemask: {exc}"
            raise CinemaskError(msg) from exc

        if response.status_code in (401, 403):
            msg = "Cinemask rejected the API key. Use a full-access key."
            raise CinemaskAuthError(msg)
        if not response.ok:
            try:
                detail = response.json().get("error")
            except ValueError:
                detail = None
            msg = detail or f"Cinemask request failed ({response.status_code})"
            raise CinemaskError(msg)
        return response.json()

    def list_requests(self):
        """Return the key owner's own requests, newest first."""
        return self._request("GET", "/api/requests", params={"mine": "true"})

    def create_request(self, media_type: str, tmdb_id):
        """Request a whole movie or show; returns the request row.

        Cinemask returns the existing open request instead of a duplicate.
        """
        return self._request(
            "POST",
            "/api/requests",
            json={"kind": KIND_BY_MEDIA_TYPE[media_type], "tmdbId": int(tmdb_id)},
        )


def save_request(user, row):
    """Upsert a Cinemask request row for a user. Returns (request, created)."""
    media_type = MEDIA_TYPE_BY_KIND.get(row.get("kind"))
    if not media_type or not row.get("id") or not row.get("tmdbId"):
        return None, False

    status = row.get("status")
    if status not in CinemaskRequestStatus.values:
        status = CinemaskRequestStatus.PENDING

    return CinemaskRequest.objects.update_or_create(
        user=user,
        request_id=row["id"],
        defaults={
            "media_type": media_type,
            "tmdb_id": str(row["tmdbId"]),
            "title": (row.get("title") or "")[:255],
            "status": status,
        },
    )


def add_to_tracking(user, media_type, tmdb_id):
    """Add a requested title to the user's list as Planning, if not tracked."""
    processor = SeerrWebhookProcessor()
    item = processor._get_or_create_tmdb_item(media_type, str(tmdb_id))
    if item:
        processor._get_or_create_user_media(
            user,
            item,
            media_type,
            Status.PLANNING.value,
        )


def sync_requests(account):
    """Refresh the user's Cinemask requests. Returns the number of rows seen."""
    user = account.user
    try:
        rows = CinemaskClient.for_account(account).list_requests()
    except CinemaskError as exc:
        account.connection_broken = isinstance(exc, CinemaskAuthError)
        account.last_error_message = str(exc)
        account.save(update_fields=["connection_broken", "last_error_message"])
        raise

    seen_ids = set()
    for row in rows:
        request, created = save_request(user, row)
        if not request:
            continue
        seen_ids.add(request.request_id)
        if (
            created
            and account.add_to_tracking
            and request.status != CinemaskRequestStatus.DECLINED
        ):
            add_to_tracking(user, request.media_type, request.tmdb_id)

    # Requests cancelled or deleted in Cinemask drop out of the list. Only
    # prune when the list wasn't truncated.
    if len(rows) < REQUEST_LIST_LIMIT:
        CinemaskRequest.objects.filter(user=user).exclude(
            request_id__in=seen_ids,
        ).delete()

    account.last_sync_at = timezone.now()
    account.connection_broken = False
    account.last_error_message = ""
    account.save(
        update_fields=["last_sync_at", "connection_broken", "last_error_message"],
    )
    return len(seen_ids)
