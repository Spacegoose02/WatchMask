from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Episode, MediaTypes, Movie, Status
from integrations import bingebandit as bingebandit_api
from integrations.imports import helpers
from integrations.imports.bingebandit import BingeBanditImporter
from integrations.models import BingeBanditAccount

HISTORY = [
    # Newest first, like the export.
    {
        "watched_at": "2026-10-20T21:00:00Z", "type": "episode", "title": "Friends",
        "year": "1994", "tmdb_id": "1668", "season": "1", "episode": "1",
        "episode_title": "Pilot", "completed": "true",
    },
    {
        "watched_at": "2026-10-19T21:00:00Z", "type": "movie", "title": "Abandoned",
        "year": "2001", "tmdb_id": "999", "season": "", "episode": "",
        "episode_title": "", "completed": "false",
    },
    {
        "watched_at": "2026-10-18T21:00:00Z", "type": "movie", "title": "The Matrix",
        "year": "1999", "tmdb_id": "603", "season": "", "episode": "",
        "episode_title": "", "completed": "true",
    },
]


def _metadata(media_type, _tmdb_id, title, _season=None):
    if media_type == MediaTypes.MOVIE.value:
        return {"title": title, "image": "movie.jpg"}
    if media_type == MediaTypes.TV.value:
        return {"title": title, "image": "tv.jpg", "last_episode_season": 10, "max_progress": 236}
    return {
        "title": "Season 1",
        "image": "season.jpg",
        "episodes": [{"episode_number": 1, "still_path": None}],
        "max_progress": 24,
    }


@patch("integrations.imports.trakt.TraktImporter._get_metadata", side_effect=_metadata)
class BingeBanditImporterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="test", password="12345")
        self.client_api = MagicMock(base_url="http://bb.local")
        self.client_api.get_history.return_value = HISTORY

    def test_imports_finished_movies_and_episodes(self, _mock_metadata):
        counts, warnings = BingeBanditImporter(self.client_api, self.user, "new").import_data()

        self.assertEqual(counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(counts[MediaTypes.EPISODE.value], 1)
        self.assertEqual(warnings, "")
        movie = Movie.objects.get(user=self.user)
        self.assertEqual(movie.item.media_id, "603")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.end_date.isoformat(), "2026-10-18T21:00:00+00:00")
        episode = Episode.objects.get(related_season__user=self.user)
        self.assertEqual(episode.item.media_id, "1668")
        self.assertEqual((episode.item.season_number, episode.item.episode_number), (1, 1))
        self.assertTrue(TV.objects.filter(user=self.user, item__media_id="1668").exists())
        self.client_api.get_library_tmdb_ids.assert_not_called()

    def test_library_filter_keeps_only_that_library(self, _mock_metadata):
        self.client_api.get_library_tmdb_ids.return_value = {"603"}

        counts, _ = BingeBanditImporter(self.client_api, self.user, "new", "4").import_data()

        self.client_api.get_library_tmdb_ids.assert_called_once_with("4")
        self.assertEqual(counts, {MediaTypes.MOVIE.value: 1})

    def test_rerun_does_not_duplicate(self, _mock_metadata):
        BingeBanditImporter(self.client_api, self.user, "new").import_data()
        BingeBanditImporter(self.client_api, self.user, "new").import_data()

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Episode.objects.filter(related_season__user=self.user).count(), 1)

    def test_api_error_aborts_import(self, _mock_metadata):
        self.client_api.get_history.side_effect = bingebandit_api.BingeBanditAuthError("bad key")

        with self.assertRaisesMessage(helpers.MediaImportError, "bad key"):
            BingeBanditImporter(self.client_api, self.user, "new").import_data()


class BingeBanditViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="test", password="12345")
        self.client.login(username="test", password="12345")

    def _connect(self):
        return BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key=helpers.encrypt("bb_key"),
            username="alice",
            webhook_usernames="alice",
        )

    @patch("integrations.bingebandit.BingeBanditClient.get_me", return_value={"username": "alice"})
    def test_connect_stores_encrypted_key_and_filters_webhooks_to_user(self, _mock_me):
        response = self.client.post(
            reverse("bingebandit_connect"),
            {"base_url": "http://bb.local/", "api_key": "bb_key"},
        )

        self.assertRedirects(response, reverse("import_data"), fetch_redirect_response=False)
        account = BingeBanditAccount.objects.get(user=self.user)
        self.assertEqual(account.base_url, "http://bb.local")
        self.assertEqual(helpers.decrypt(account.api_key), "bb_key")
        self.assertEqual(account.webhook_usernames, "alice")

    @patch(
        "integrations.bingebandit.BingeBanditClient.get_me",
        side_effect=bingebandit_api.BingeBanditAuthError("bad key"),
    )
    def test_connect_rejects_bad_key(self, _mock_me):
        self.client.post(
            reverse("bingebandit_connect"),
            {"base_url": "http://bb.local", "api_key": "nope"},
        )
        self.assertFalse(BingeBanditAccount.objects.filter(user=self.user).exists())

    def test_disconnect_removes_account(self):
        self._connect()
        self.client.post(reverse("bingebandit_disconnect"))
        self.assertFalse(BingeBanditAccount.objects.filter(user=self.user).exists())

    @patch(
        "integrations.bingebandit.BingeBanditClient.get_libraries",
        return_value=[{"id": 1, "name": "Movies", "kind": "movies"}],
    )
    def test_status_lists_libraries(self, _mock_libraries):
        self._connect()
        data = self.client.get(reverse("bingebandit_status")).json()
        self.assertEqual(data["state"], "connected")
        self.assertEqual(data["libraries"], [{"id": 1, "name": "Movies", "kind": "movies"}])

    @patch("integrations.tasks.import_bingebandit.delay")
    def test_import_saves_usernames_and_queues_task(self, mock_delay):
        account = self._connect()
        self.client.post(
            reverse("import_bingebandit"),
            {
                "library": "4",
                "mode": "new",
                "frequency": "once",
                "bingebandit_usernames": "alice, Bob, ALICE",
            },
        )

        account.refresh_from_db()
        self.assertEqual(account.webhook_usernames, "alice, Bob")
        mock_delay.assert_called_once_with(library="4", user_id=self.user.id, mode="new")

    def test_import_page_shows_connect_form_then_import_card(self):
        response = self.client.get(reverse("import_data"))
        self.assertContains(response, reverse("bingebandit_connect"))

        self._connect()
        response = self.client.get(reverse("import_data"))
        self.assertContains(response, reverse("bingebandit_status"))
        self.assertContains(response, reverse("bingebandit_disconnect"))
        self.assertContains(response, 'name="bingebandit_usernames"')
        self.assertContains(response, 'value="alice"')
