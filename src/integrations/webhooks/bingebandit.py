import logging

from app import live_playback
from app.models import MediaTypes, Sources
from integrations.models import BingeBanditResumePoint

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)

# BingeBandit events mapped to the normalised live playback events.
# https://github.com/Spacegoose02/BingeBandit/blob/main/docs/API.md#webhooks
BINGEBANDIT_LIVE_EVENT_MAP = {
    "player.play": "media.play",
    "player.resume": "media.play",
    "player.pause": "media.pause",
    "player.stop": "media.stop",
    "playback.finished": "media.scrobble",
}

# Events that update tracking. playback.stop is left out: playback.start has
# already marked the item in progress and playback.finished marks it watched,
# so tracking a stop would only open a new in-progress entry after a finish.
BINGEBANDIT_TRACKING_EVENTS = ("playback.start", "playback.finished")

# Events that save or clear the resume point shown under Continue Watching.
BINGEBANDIT_RESUME_EVENTS = ("playback.stop", "playback.finished")

# A stop this early, or with this little left, isn't worth resuming.
RESUME_MIN_POSITION_SECONDS = 60
RESUME_MAX_PERCENT = 95


def _seconds(value) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _positive_id(value) -> str | None:
    """BingeBandit sends 0 when it has no TMDB match."""
    try:
        return str(int(value)) if int(value) > 0 else None
    except (TypeError, ValueError):
        return None


class BingeBanditWebhookProcessor(BaseWebhookProcessor):
    """Processor for BingeBandit's native JSON webhooks."""

    SOURCE_LABEL = "bingebandit"

    MEDIA_TYPE_MAPPING = {
        "episode": MediaTypes.TV.value,
        "movie": MediaTypes.MOVIE.value,
    }

    def process_payload(self, payload, user):
        """Process the incoming BingeBandit webhook payload."""
        event_type = payload.get("event")
        if not self._is_supported_event(event_type):
            logger.debug("Ignoring BingeBandit webhook event type: %s", event_type)
            return

        ids = self._extract_external_ids(payload)
        self._update_live_playback_state(payload, user, ids)
        if event_type in BINGEBANDIT_RESUME_EVENTS and ids["tmdb_id"]:
            self._update_resume_point(payload, user, ids)

        if event_type not in BINGEBANDIT_TRACKING_EVENTS:
            return

        if not ids["tmdb_id"]:
            logger.warning(
                "Ignoring BingeBandit webhook because no TMDB ID was found.",
            )
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return (
            event_type in BINGEBANDIT_LIVE_EVENT_MAP
            or event_type in BINGEBANDIT_TRACKING_EVENTS
            or event_type in BINGEBANDIT_RESUME_EVENTS
        )

    def _is_played(self, payload):
        return payload.get("event") == "playback.finished"

    def _get_media_type(self, payload):
        return self.MEDIA_TYPE_MAPPING.get((payload.get("data") or {}).get("kind"))

    def _get_media_title(self, payload):
        data = payload.get("data") or {}
        media_type = self._get_media_type(payload)
        if media_type == MediaTypes.TV.value:
            season, episode = self._extract_season_episode_from_payload(payload)
            if season is None or episode is None:
                return data.get("title")
            return f"{data.get('title')} S{season:02d}E{episode:02d}"
        if media_type == MediaTypes.MOVIE.value:
            title, year = data.get("title"), data.get("year")
            return f"{title} ({year})" if title and year else title
        return None

    def _extract_external_ids(self, payload):
        data = payload.get("data") or {}
        # Episodes carry the show's TMDB ID, which is what TV tracking needs.
        key = "showTmdbId" if data.get("kind") == "episode" else "tmdbId"
        return {
            "tmdb_id": _positive_id(data.get(key)),
            "imdb_id": None,
            "tvdb_id": None,
        }

    def _extract_season_episode_from_payload(self, payload):
        data = payload.get("data") or {}
        try:
            return int(data["season"]), int(data["episode"])
        except (KeyError, TypeError, ValueError):
            return None, None

    def _extract_series_title(self, payload):
        if self._get_media_type(payload) == MediaTypes.TV.value:
            return (payload.get("data") or {}).get("title")
        return None

    # -- Resume points ----------------------------------------------------

    def _update_resume_point(self, payload, user, ids):
        """Save where a play stopped part way, or clear it once finished."""
        media_type = self._get_media_type(payload)
        if not media_type:
            return
        data = payload.get("data") or {}
        key = {"user": user, "media_type": media_type, "tmdb_id": ids["tmdb_id"]}
        position = _seconds(data.get("position")) or 0
        duration = _seconds(data.get("duration")) or 0
        item_id = _positive_id(data.get("itemId"))

        if payload.get("event") == "playback.finished":
            BingeBanditResumePoint.objects.filter(**key).delete()
            return
        if not item_id:
            return
        if (
            not duration
            or position < RESUME_MIN_POSITION_SECONDS
            or position * 100 >= duration * RESUME_MAX_PERCENT
        ):
            # Only clear the point if it's for this item, so a short look at
            # another episode keeps the show's resume point.
            BingeBanditResumePoint.objects.filter(**key, item_id=item_id).delete()
            return

        season, episode = self._extract_season_episode_from_payload(payload)
        BingeBanditResumePoint.objects.update_or_create(
            **key,
            defaults={
                "item_id": int(item_id),
                "title": (data.get("title") or "")[:255],
                "episode_title": (data.get("episodeTitle") or "")[:255],
                "season_number": season,
                "episode_number": episode,
                "position_seconds": position,
                "duration_seconds": duration,
            },
        )

    # -- Live playback --------------------------------------------------

    def _update_live_playback_state(self, payload, user, ids):
        """Update cache-backed live playback state for home-page UI."""
        event_type = BINGEBANDIT_LIVE_EVENT_MAP.get(payload.get("event"))
        if not event_type:
            return

        data = payload.get("data") or {}
        kind = data.get("kind")
        if kind == "movie":
            playback_media_type = MediaTypes.MOVIE.value
        elif kind == "episode":
            playback_media_type = MediaTypes.EPISODE.value
        else:
            return

        season_number, episode_number = self._extract_season_episode_from_payload(
            payload,
        )
        is_episode = playback_media_type == MediaTypes.EPISODE.value
        live_playback.apply_playback_event(
            user_id=user.id,
            event_type=event_type,
            playback_media_type=playback_media_type,
            media_id=ids["tmdb_id"],
            source=Sources.TMDB.value,
            rating_key=str(data.get("itemId") or "").strip() or None,
            title=data.get("episodeTitle") if is_episode else data.get("title"),
            series_title=data.get("title") if is_episode else None,
            episode_title=data.get("episodeTitle") if is_episode else None,
            season_number=season_number,
            episode_number=episode_number,
            view_offset_seconds=_seconds(data.get("position")),
            duration_seconds=_seconds(data.get("duration")),
        )
