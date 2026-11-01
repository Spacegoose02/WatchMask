import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources
from integrations import bingebandit
from integrations.imports import helpers
from integrations.models import (
    BingeBanditAccount,
    BingeBanditLibraryItem,
    BingeBanditResumePoint,
)
from integrations.tests.test_webhooks_bingebandit import (
    EPISODE_DATA,
    MOVIE_DATA,
    MOVIE_METADATA,
    _event,
)

LIBRARY_PAGES = {
    1: [
        {"id": 10, "kind": "movie", "tmdbId": 603},
        {"id": 11, "kind": "movie", "tmdbId": 0},
    ],
    2: [
        {"id": 20, "kind": "show", "tmdbId": 1668},
        {"id": 21, "kind": "season", "tmdbId": 1668},
    ],
}


def _library_page(_self, path, params=None, timeout=30):
    class Response:
        def json(self):
            if path == "/libraries":
                return [{"id": 1, "name": "Movies"}, {"id": 2, "name": "Shows"}]
            items = LIBRARY_PAGES[int(path.split("/")[2])]
            return {"items": items, "total": len(items)}

    return Response()


class BingeBanditLibraryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="test", password="12345")
        self.client.login(username="test", password="12345")
        self.account = BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key=helpers.encrypt("bb_key"),
            username="alice",
            # Collection sync looks titles up on TMDB; it has its own tests.
            sync_collection=False,
        )

    @patch("integrations.bingebandit.BingeBanditClient._get", _library_page)
    def test_sync_indexes_matched_movies_and_shows(self):
        self.assertEqual(bingebandit.sync_library(self.account), 2)

        self.assertEqual(
            bingebandit.library_item_ids(self.user),
            {
                (MediaTypes.MOVIE.value, "603"): 10,
                (MediaTypes.TV.value, "1668"): 20,
            },
        )
        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.library_synced_at)

    @patch("integrations.bingebandit.BingeBanditClient._get", _library_page)
    def test_sync_drops_items_no_longer_in_the_library(self):
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="1", item_id=1,
        )
        bingebandit.library_item_ids(self.user)  # warm the cache

        bingebandit.sync_library(self.account)

        self.assertNotIn(
            (MediaTypes.MOVIE.value, "1"),
            bingebandit.library_item_ids(self.user),
        )

    def _button(self, media_type, tmdb_id):
        return self.client.get(
            reverse("bingebandit_play_button"),
            {"media_type": media_type, "tmdb_id": tmdb_id},
        ).content.decode()

    def test_play_button_links_to_library_item(self):
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="603", item_id=10,
        )
        html = self._button(MediaTypes.MOVIE.value, "603")
        self.assertIn('href="http://bb.local/item/10"', html)
        self.assertIn("Play", html)

    def test_play_button_prefers_resume_point(self):
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.TV.value, tmdb_id="1668", item_id=20,
        )
        BingeBanditResumePoint.objects.create(
            user=self.user,
            media_type=MediaTypes.TV.value,
            tmdb_id="1668",
            item_id=77,
            title="Friends",
            season_number=1,
            episode_number=2,
            position_seconds=630,
            duration_seconds=1320,
        )
        html = self._button(MediaTypes.TV.value, "1668")
        self.assertIn('href="http://bb.local/play/77?t=630"', html)
        self.assertIn("Resume S01E02 · 10:30", html)

    def test_play_button_offers_request_when_not_in_library(self):
        html = self._button(MediaTypes.MOVIE.value, "603")
        self.assertIn("Request on BingeBandit", html)
        self.assertIn(reverse("bingebandit_request", args=[MediaTypes.MOVIE.value, "603"]), html)

    def test_card_badge_filter(self):
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="603", item_id=10,
        )
        template = Template("{% load app_tags %}{{ item|in_bingebandit_library:user }}")
        in_library = Item(media_id="603", source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value)
        other = Item(media_id="604", source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value)

        self.assertEqual(template.render(Context({"item": in_library, "user": self.user})), "True")
        self.assertEqual(template.render(Context({"item": other, "user": self.user})), "False")

    def test_disconnect_clears_library_and_resume_points(self):
        BingeBanditLibraryItem.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="603", item_id=10,
        )
        BingeBanditResumePoint.objects.create(
            user=self.user, media_type=MediaTypes.MOVIE.value, tmdb_id="603",
            item_id=10, position_seconds=600, duration_seconds=6000,
        )
        self.client.post(reverse("bingebandit_disconnect"))
        self.assertFalse(BingeBanditLibraryItem.objects.filter(user=self.user).exists())
        self.assertFalse(BingeBanditResumePoint.objects.filter(user=self.user).exists())


@patch("app.live_playback.apply_playback_event")
@patch("app.providers.tmdb.movie", return_value=MOVIE_METADATA)
class BingeBanditResumeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test", password="12345", token="test-token",
        )
        self.client.login(username="test", password="12345")
        self.account = BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key=helpers.encrypt("bb_key"),
            username="alice",
        )
        self.url = reverse("bingebandit_webhook", kwargs={"token": "test-token"})

    def _post(self, event, data):
        self.client.post(self.url, data=json.dumps(_event(event, data)), content_type="application/json")

    def test_stop_part_way_saves_resume_point(self, *_mocks):
        self._post("playback.stop", {**MOVIE_DATA, "position": 2400})
        point = BingeBanditResumePoint.objects.get(user=self.user)
        self.assertEqual(point.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(point.tmdb_id, "603")
        self.assertEqual(point.item_id, 42)
        self.assertEqual(point.position_seconds, 2400)

    def test_episode_resume_point_is_kept_per_show(self, *_mocks):
        self._post("playback.stop", {**EPISODE_DATA, "position": 300})
        self._post("playback.stop", {**EPISODE_DATA, "itemId": 78, "episode": 2, "position": 400})
        point = BingeBanditResumePoint.objects.get(user=self.user)
        self.assertEqual(point.tmdb_id, "1668")
        self.assertEqual((point.item_id, point.episode_code), (78, "S01E02"))

    def test_short_look_at_another_episode_keeps_resume_point(self, *_mocks):
        self._post("playback.stop", {**EPISODE_DATA, "position": 300})
        self._post("playback.stop", {**EPISODE_DATA, "itemId": 78, "position": 40})
        self.assertEqual(BingeBanditResumePoint.objects.get(user=self.user).item_id, 77)

    def test_stop_near_the_end_clears_resume_point(self, *_mocks):
        self._post("playback.stop", {**MOVIE_DATA, "position": 2400})
        self._post("playback.stop", {**MOVIE_DATA, "position": 8000})
        self.assertFalse(BingeBanditResumePoint.objects.exists())

    def test_finished_clears_resume_point(self, *_mocks):
        self._post("playback.stop", {**MOVIE_DATA, "position": 2400})
        self._post("playback.finished", MOVIE_DATA)
        self.assertFalse(BingeBanditResumePoint.objects.exists())

    def test_home_shows_continue_watching(self, *_mocks):
        self._post("playback.stop", {**MOVIE_DATA, "position": 2400})
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Continue Watching")
        self.assertContains(response, 'href="http://bb.local/play/42?t=2400"')
