"""LootDen game server: API client and play-time sync.

LootDen (https://github.com/Spacegoose02/lootden) serves a household's PC and
emulated games. Each person has a personal API token; ``/api/me/games`` lists
the games they installed or played, per device, with play time.
"""

import logging
from collections import defaultdict
from datetime import datetime

import requests
from django.utils import timezone

from app.models import Game, Item, MediaTypes, Sources, Status
from integrations.imports import helpers

logger = logging.getLogger(__name__)

SYNC_NOTE = "Synced from LootDen"


class LootDenError(Exception):
    """LootDen request failed."""


class LootDenAuthError(LootDenError):
    """LootDen rejected the API token."""


class LootDenClient:
    """Talk to a LootDen server with a person's API token."""

    def __init__(self, base_url: str, api_token: str):
        """Initialize with the server URL and a personal API token."""
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token

    def _get(self, path, timeout=30):
        try:
            response = requests.get(
                f"{self.base_url}/api{path}",
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            msg = f"Could not reach LootDen: {exc}"
            raise LootDenError(msg) from exc
        if response.status_code in (401, 403):
            msg = "LootDen API token is invalid or was revoked."
            raise LootDenAuthError(msg)
        if not response.ok:
            msg = f"LootDen request failed ({response.status_code}) for {path}"
            raise LootDenError(msg)
        return response.json()

    def get_me(self):
        """Return the person the token belongs to, or None for a service token."""
        return self._get("/me").get("user")

    def get_game_states(self):
        """Return the person's installs and play time, one row per game and device."""
        return self._get("/me/games")

    def get_game(self, game_id):
        """Return a game's details, including its metadata match."""
        return self._get(f"/games/{requests.utils.quote(str(game_id), safe='')}")


def client_for(account):
    """Build a client from a stored account."""
    return LootDenClient(account.base_url, helpers.decrypt(account.api_token))


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def aggregate_states(states):
    """Sum play time across devices: {game_id: {seconds, first, last}}."""
    games = defaultdict(lambda: {"seconds": 0, "first": None, "last": None})
    for state in states or []:
        game_id = state.get("gameId")
        if not game_id:
            continue
        entry = games[game_id]
        entry["seconds"] += max(0, int(state.get("playtimeSeconds") or 0))
        for stamp in (_parse_time(state.get("installedAt")), _parse_time(state.get("lastPlayedAt"))):
            if stamp and (entry["first"] is None or stamp < entry["first"]):
                entry["first"] = stamp
        last_played = _parse_time(state.get("lastPlayedAt"))
        if last_played and (entry["last"] is None or last_played > entry["last"]):
            entry["last"] = last_played
    return dict(games)


def igdb_id_for(game):
    """Return the IGDB id LootDen matched a game to, if any."""
    from app.providers.igdb import ExternalGameSource, external_game

    metadata = game.get("metadata") or {}
    if metadata.get("provider") == "igdb" and str(metadata.get("providerId") or "").isdigit():
        return str(metadata["providerId"])
    steam_app_id = metadata.get("steamAppId")
    if steam_app_id:
        igdb_id = external_game(str(steam_app_id), ExternalGameSource.STEAM)
        if igdb_id:
            return str(igdb_id)
    return None


def _item_for_igdb(igdb_id, fallback_title):
    from app.providers import services

    item = Item.objects.filter(
        source=Sources.IGDB.value,
        media_type=MediaTypes.GAME.value,
        media_id=igdb_id,
    ).first()
    if item:
        return item
    metadata = services.get_media_metadata(MediaTypes.GAME.value, igdb_id, Sources.IGDB.value)
    item, _ = Item.objects.get_or_create(
        media_id=igdb_id,
        source=Sources.IGDB.value,
        media_type=MediaTypes.GAME.value,
        defaults={
            "title": metadata.get("title") or fallback_title,
            "image": metadata.get("image") or "",
        },
    )
    return item


def _update_tracker(user, item, minutes, first, last):
    """Create or advance the user's Game entry. Returns "created", "updated" or None.

    Play time only goes up, so hours logged by hand or by another source are
    never lowered, and a status set by hand (Completed, Dropped, Paused) is
    kept. A game sitting in Planning moves to In progress once it's played.
    """
    game = Game.objects.filter(user=user, item=item).order_by("-created_at", "-id").first()
    if game is None:
        Game.objects.create(
            user=user,
            item=item,
            status=Status.IN_PROGRESS.value if minutes else Status.PLANNING.value,
            progress=minutes,
            start_date=first if minutes else None,
            notes=SYNC_NOTE,
        )
        return "created"

    changed = False
    if minutes > (game.progress or 0):
        game.progress = minutes
        changed = True
    if minutes and game.status == Status.PLANNING.value:
        game.status = Status.IN_PROGRESS.value
        if not game.start_date:
            game.start_date = first or last
        changed = True
    if changed:
        game.save()
        return "updated"
    return None


def sync(account):
    """Pull the person's LootDen games and play time into Watchmask.

    Returns counts of games created, updated, and left unmatched (no IGDB id).
    """
    from integrations.models import LootDenGame

    client = client_for(account)
    user = account.user
    games = aggregate_states(client.get_game_states())
    links = {link.game_id: link for link in LootDenGame.objects.filter(user=user)}
    counts = {"created": 0, "updated": 0, "unmatched": 0}

    for game_id, totals in games.items():
        link = links.get(game_id) or LootDenGame(user=user, game_id=game_id)
        minutes = totals["seconds"] // 60
        unchanged = (
            link.pk
            and link.item_id
            and link.playtime_seconds == totals["seconds"]
            and link.last_played_at == totals["last"]
        )
        if unchanged:
            continue

        if not link.item_id:
            try:
                detail = client.get_game(game_id)
            except LootDenError as exc:
                logger.info("Skipping LootDen game %s: %s", game_id, exc)
                continue
            link.title = (detail.get("title") or "")[:500]
            igdb_id = igdb_id_for(detail)
            if igdb_id:
                try:
                    link.item = _item_for_igdb(igdb_id, link.title or game_id)
                except Exception as exc:
                    logger.info("No IGDB item for LootDen game %s: %s", game_id, exc)

        link.playtime_seconds = totals["seconds"]
        link.first_seen_at = totals["first"]
        link.last_played_at = totals["last"]
        link.save()

        if not link.item_id:
            counts["unmatched"] += 1
            continue
        result = _update_tracker(user, link.item, minutes, totals["first"], totals["last"])
        if result:
            counts[result] += 1

    account.last_synced_at = timezone.now()
    account.last_error = ""
    account.save(update_fields=["last_synced_at", "last_error"])
    return counts


def game_link(user, item):
    """Return the LootDen page for an item the user has on LootDen, or None."""
    from integrations.models import LootDenGame

    account = getattr(user, "lootden_account", None)
    if not account:
        return None
    link = LootDenGame.objects.filter(user=user, item=item).first()
    if not link:
        return None
    return {"url": account.game_url(link.game_id), "link": link}
