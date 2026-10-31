import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import (
    TV,
    CollectionEntry,
    Game,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from integrations import bingebandit, lootden
from integrations.imports import helpers
from integrations.models import (
    BingeBanditAccount,
    BingeBanditLibraryItem,
    CollectionSourceState,
    LootDenAccount,
    LootDenGame,
)
from lists import smart_rules
from lists.models import CustomList


def _movie_item(tmdb_id, title="Movie"):
    return Item.objects.create(
        media_id=tmdb_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        title=title,
        image="http://img",
    )


class BingeBanditTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="test", password="12345", token="tok",
        )
        self.client.login(username="test", password="12345")
        self.account = BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key=helpers.encrypt("bb_key"),
            username="alice",
        )


class BingeBanditCollectionTests(BingeBanditTestCase):
    def test_server_titles_become_collected(self):
        matrix = _movie_item("603", "The Matrix")
        bingebandit.sync_collection(self.user, {(MediaTypes.MOVIE.value, "603")})

        self.assertTrue(CollectionEntry.objects.filter(user=self.user, item=matrix).exists())
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user, item=matrix, source="bingebandit",
            ).exists(),
        )

    def test_removed_titles_are_uncollected(self):
        matrix = _movie_item("603")
        bingebandit.sync_collection(self.user, {(MediaTypes.MOVIE.value, "603")})
        bingebandit.sync_collection(self.user, set())

        self.assertFalse(CollectionEntry.objects.filter(user=self.user, item=matrix).exists())

    @patch("app.providers.services.get_media_metadata")
    def test_new_titles_are_looked_up_up_to_the_limit(self, mock_metadata):
        mock_metadata.return_value = {"title": "New", "image": "http://img"}
        keys = {(MediaTypes.MOVIE.value, str(tmdb_id)) for tmdb_id in (1, 2, 3)}

        added, _ = bingebandit.sync_collection(self.user, keys, new_items_limit=2)

        self.assertEqual(added, 2)
        self.assertEqual(mock_metadata.call_count, 2)
        # The next sync picks up the rest.
        added, _ = bingebandit.sync_collection(self.user, keys, new_items_limit=2)
        self.assertEqual(added, 1)

    def test_turning_collection_off_clears_it(self):
        matrix = _movie_item("603")
        bingebandit.sync_collection(self.user, {(MediaTypes.MOVIE.value, "603")})

        self.client.post(reverse("bingebandit_settings"), {"push_watched": "on"})

        self.account.refresh_from_db()
        self.assertFalse(self.account.sync_collection)
        self.assertTrue(self.account.push_watched)
        self.assertFalse(CollectionEntry.objects.filter(user=self.user, item=matrix).exists())


class BingeBanditLibraryListTests(BingeBanditTestCase):
    def test_library_smart_rule_lists_everything_in_the_library(self):
        kids = _movie_item("10", "Kids Movie")
        grown = _movie_item("20", "Grown-up Movie")
        for tmdb_id, library in (("10", "Kids Movies"), ("20", "Movies")):
            BingeBanditLibraryItem.objects.create(
                user=self.user,
                media_type=MediaTypes.MOVIE.value,
                tmdb_id=tmdb_id,
                item_id=int(tmdb_id),
                library_name=library,
            )
        bingebandit.sync_collection(
            self.user,
            {(MediaTypes.MOVIE.value, "10"), (MediaTypes.MOVIE.value, "20")},
        )
        rules = smart_rules.normalize_rule_payload(
            {"media_types": [MediaTypes.MOVIE.value], "library": "Kids Movies"},
            self.user,
        )

        self.assertEqual(rules["library"], "Kids Movies")
        # Neither movie is tracked; the library list still shows the kids one.
        ids = smart_rules.collect_matching_item_ids(self.user, rules)
        self.assertEqual(ids, {kids.id})
        self.assertNotIn(grown.id, ids)

    def test_saved_rules_keep_the_library(self):
        custom_list = CustomList.objects.create(
            name="Kids", owner=self.user, is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
        )
        rules = smart_rules.normalize_rule_payload({"library": "Kids Movies"}, self.user)
        self.assertEqual(
            smart_rules.saved_filters(rules, custom_list)["library"], "Kids Movies",
        )


class BingeBanditRequestTests(BingeBanditTestCase):
    @patch("integrations.bingebandit.requests.request")
    def test_request_posts_title_to_the_server(self, mock_request):
        mock_request.return_value = MagicMock(status_code=201, ok=True, content=b"{}")
        mock_request.return_value.json.return_value = {}
        _movie_item("603", "The Matrix")

        response = self.client.post(
            reverse("bingebandit_request", args=[MediaTypes.MOVIE.value, "603"]),
        )

        self.assertContains(response, "Requested on BingeBandit")
        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", "http://bb.local/api/requests"))
        self.assertEqual(kwargs["json"]["kind"], "movie")
        self.assertEqual(kwargs["json"]["tmdbId"], 603)
        self.assertEqual(kwargs["json"]["title"], "The Matrix")

    @patch("integrations.bingebandit.requests.request")
    def test_request_already_on_server_shows_the_reason(self, mock_request):
        mock_request.return_value = MagicMock(status_code=409, ok=False)

        response = self.client.post(
            reverse("bingebandit_request", args=[MediaTypes.TV.value, "1668"]),
        )

        self.assertContains(response, "already on the BingeBandit server")


class BingeBanditWatchedPushTests(BingeBanditTestCase):
    def setUp(self):
        super().setUp()
        self.account.push_watched = True
        self.account.save()
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="603", item_id=10,
        )

    @patch("integrations.bingebandit.requests.request")
    def test_completing_a_movie_marks_it_watched_on_the_server(self, mock_request):
        mock_request.return_value = MagicMock(status_code=204, ok=True)
        item = _movie_item("603")

        with self.captureOnCommitCallbacks(execute=True), patch(
            "integrations.tasks.push_bingebandit_watched.delay",
            side_effect=lambda *args: bingebandit.push_watched(self.user, args[1], args[2]),
        ):
            Movie.objects.create(user=self.user, item=item, status=Status.COMPLETED.value)

        mock_request.assert_called_once()
        self.assertEqual(mock_request.call_args.args, ("POST", "http://bb.local/api/items/10/watched"))

    @patch("integrations.tasks.push_bingebandit_watched.delay")
    def test_in_progress_is_not_pushed(self, mock_delay):
        item = _movie_item("603")
        with self.captureOnCommitCallbacks(execute=True):
            Movie.objects.create(user=self.user, item=item, status=Status.IN_PROGRESS.value)
        mock_delay.assert_not_called()

    @patch("integrations.tasks.push_bingebandit_watched.delay")
    def test_nothing_is_pushed_when_turned_off(self, mock_delay):
        self.account.push_watched = False
        self.account.save()
        item = Item.objects.create(
            media_id="1668", source=Sources.TMDB.value, media_type=MediaTypes.TV.value,
            title="Friends", image="http://img",
        )
        with self.captureOnCommitCallbacks(execute=True):
            TV.objects.create(user=self.user, item=item, status=Status.COMPLETED.value)
        mock_delay.assert_not_called()


class BingeBanditSignatureTests(BingeBanditTestCase):
    def setUp(self):
        super().setUp()
        self.account.webhook_secret = helpers.encrypt("s3cret")
        self.account.save()
        self.url = reverse("bingebandit_webhook", kwargs={"token": "tok"})
        self.body = json.dumps({"event": "test", "data": {"user": "alice"}}).encode()

    def _post(self, signature):
        headers = {"HTTP_X_BINGEBANDIT_SIGNATURE": signature} if signature else {}
        return self.client.post(
            self.url, data=self.body, content_type="application/json", **headers,
        )

    @patch("integrations.tasks.process_webhook.delay")
    def test_signed_webhook_is_accepted(self, mock_delay):
        digest = hmac.new(b"s3cret", self.body, hashlib.sha256).hexdigest()
        self.assertEqual(self._post(f"sha256={digest}").status_code, 200)
        mock_delay.assert_called_once()

    @patch("integrations.tasks.process_webhook.delay")
    def test_wrong_or_missing_signature_is_rejected(self, mock_delay):
        self.assertEqual(self._post("sha256=deadbeef").status_code, 401)
        self.assertEqual(self._post("").status_code, 401)
        mock_delay.assert_not_called()


GAME_STATES = [
    {"gameId": "g1", "device": "pc", "installedAt": "2026-01-02T10:00:00Z",
     "uninstalledAt": None, "installPath": None, "playtimeSeconds": 3600,
     "lastPlayedAt": "2026-03-01T20:00:00Z"},
    {"gameId": "g1", "device": "deck", "installedAt": "2026-02-01T10:00:00Z",
     "uninstalledAt": None, "installPath": None, "playtimeSeconds": 1800,
     "lastPlayedAt": "2026-03-05T20:00:00Z"},
    {"gameId": "g2", "device": "pc", "installedAt": "2026-03-10T10:00:00Z",
     "uninstalledAt": None, "installPath": None, "playtimeSeconds": 0,
     "lastPlayedAt": None},
    {"gameId": "g3", "device": "pc", "installedAt": "2026-03-10T10:00:00Z",
     "uninstalledAt": None, "installPath": None, "playtimeSeconds": 600,
     "lastPlayedAt": "2026-03-11T10:00:00Z"},
]

GAMES = {
    "g1": {"id": "g1", "title": "Hollow Knight", "metadata": {"provider": "igdb", "providerId": "14593"}},
    "g2": {"id": "g2", "title": "Celeste", "metadata": {"provider": "igdb", "providerId": "26226"}},
    "g3": {"id": "g3", "title": "Homebrew", "metadata": None},
}


def _lootden_get(_self, path, timeout=30):
    if path == "/me":
        return {"user": {"username": "kid"}}
    if path == "/me/games":
        return GAME_STATES
    return GAMES[path.rsplit("/", 1)[1]]


@patch("integrations.lootden.LootDenClient._get", _lootden_get)
class LootDenSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kid", password="12345")
        self.client.login(username="kid", password="12345")
        self.account = LootDenAccount.objects.create(
            user=self.user,
            base_url="http://loot.local",
            api_token=helpers.encrypt("tok"),
            username="kid",
        )
        for igdb_id, title in (("14593", "Hollow Knight"), ("26226", "Celeste")):
            Item.objects.create(
                media_id=igdb_id, source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value, title=title, image="http://img",
            )

    def _game(self, igdb_id):
        return Game.objects.get(user=self.user, item__media_id=igdb_id)

    def test_sync_adds_games_with_play_time(self):
        counts = lootden.sync(self.account)

        self.assertEqual(counts, {"created": 2, "updated": 0, "unmatched": 1})
        hollow = self._game("14593")
        self.assertEqual(hollow.progress, 90)  # 1h on PC + 30m on the Deck
        self.assertEqual(hollow.status, Status.IN_PROGRESS.value)
        self.assertEqual(self._game("26226").status, Status.PLANNING.value)
        self.assertEqual(LootDenGame.objects.get(game_id="g3").title, "Homebrew")

    def test_sync_never_lowers_hours_or_overrides_a_set_status(self):
        lootden.sync(self.account)
        hollow = self._game("14593")
        hollow.progress = 300
        hollow.status = Status.COMPLETED.value
        hollow.save()
        LootDenGame.objects.filter(game_id="g1").update(playtime_seconds=0)

        lootden.sync(self.account)

        hollow.refresh_from_db()
        self.assertEqual(hollow.progress, 300)
        self.assertEqual(hollow.status, Status.COMPLETED.value)

    def test_unchanged_games_are_skipped(self):
        lootden.sync(self.account)
        with patch("integrations.lootden._update_tracker") as mock_update:
            lootden.sync(self.account)
        mock_update.assert_not_called()

    def test_play_button_links_to_the_game(self):
        lootden.sync(self.account)
        response = self.client.get(reverse("lootden_play_button"), {"igdb_id": "14593"})
        self.assertContains(response, 'href="http://loot.local/games/g1"')
        self.assertContains(response, "Play on LootDen")

    def test_play_button_empty_for_games_not_on_lootden(self):
        response = self.client.get(reverse("lootden_play_button"), {"igdb_id": "999"})
        self.assertEqual(response.content, b"")

    @patch("integrations.tasks.sync_lootden.delay")
    def test_connect_stores_token_and_queues_sync(self, mock_delay):
        self.account.delete()
        response = self.client.post(
            reverse("lootden_connect"),
            {"base_url": "http://loot.local/", "api_token": "tok"},
        )
        self.assertRedirects(response, reverse("import_data"), fetch_redirect_response=False)
        account = LootDenAccount.objects.get(user=self.user)
        self.assertEqual(account.base_url, "http://loot.local")
        self.assertEqual(helpers.decrypt(account.api_token), "tok")
        mock_delay.assert_called_once_with(user_id=self.user.id)
