import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import MediaTypes, Movie, Status
from integrations.models import BingeBanditAccount

MOVIE_DATA = {
    "user": "alice",
    "userId": 1,
    "itemId": 42,
    "kind": "movie",
    "title": "The Matrix",
    "year": 1999,
    "client": "Web",
    "position": 120,
    "duration": 8160,
    "tmdbId": 603,
}

EPISODE_DATA = {
    "user": "alice",
    "userId": 1,
    "itemId": 77,
    "kind": "episode",
    "title": "Friends",
    "episodeTitle": "The One Where Monica Gets a Roommate",
    "season": 1,
    "episode": 1,
    "position": 1300,
    "duration": 1320,
    "tmdbId": 85987,
    "showTmdbId": 1668,
}

MOVIE_METADATA = {
    "media_id": "603",
    "title": "The Matrix",
    "image": "https://example.com/matrix.jpg",
    "max_progress": 1,
}


def _event(event, data):
    return {
        "event": event,
        "timestamp": "2026-10-23T20:15:00Z",
        "server": "BingeBandit",
        "data": data,
    }


class BingeBanditWebhookViewTests(TestCase):
    """Tests for the BingeBandit webhook view (auth, payload, user filter)."""

    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="testuser", token="test-token",
        )
        self.url = reverse("bingebandit_webhook", kwargs={"token": "test-token"})

    def _post(self, payload, url=None):
        return self.client.post(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_invalid_token(self):
        url = reverse("bingebandit_webhook", kwargs={"token": "invalid-token"})
        self.assertEqual(self._post({}, url).status_code, 401)

    def test_missing_body(self):
        response = self.client.post(self.url, data="", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_plex_style_format(self):
        payload = json.dumps({"event": "media.play", "Metadata": {}})
        response = self.client.post(self.url, data={"payload": payload})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"BingeBandit JSON", response.content)

    def test_invalid_json(self):
        response = self.client.post(
            self.url, data="not-json", content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_non_object_json(self):
        self.assertEqual(self._post([1, 2]).status_code, 400)

    @patch("integrations.tasks.process_webhook.delay")
    def test_valid_payload_is_queued(self, mock_delay):
        payload = _event("playback.start", MOVIE_DATA)
        self.assertEqual(self._post(payload).status_code, 200)
        mock_delay.assert_called_once_with("bingebandit", payload, self.user.id)

    @patch("integrations.tasks.process_webhook.delay")
    def test_users_filter_skips_other_users(self, mock_delay):
        url = f"{self.url}?users=bob,Carol"
        response = self._post(_event("playback.start", MOVIE_DATA), url)
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()

    @patch("integrations.tasks.process_webhook.delay")
    def test_users_filter_accepts_listed_user(self, mock_delay):
        url = f"{self.url}?users=bob,%20ALICE"
        response = self._post(_event("playback.start", MOVIE_DATA), url)
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once()

    @patch("integrations.tasks.process_webhook.delay")
    def test_saved_usernames_filter_events(self, mock_delay):
        account = BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key="x",
            webhook_usernames="bob",
        )
        self._post(_event("playback.start", MOVIE_DATA))
        mock_delay.assert_not_called()

        account.webhook_usernames = "bob, Alice"
        account.save()
        self._post(_event("playback.start", MOVIE_DATA))
        mock_delay.assert_called_once()


class BingeBanditWebhookProcessingTests(TestCase):
    """Tests for BingeBandit webhook processing."""

    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="testuser", token="test-token",
        )
        self.url = reverse("bingebandit_webhook", kwargs={"token": "test-token"})

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    @patch("app.providers.tmdb.movie", return_value=MOVIE_METADATA)
    def test_movie_start_marks_in_progress(self, _mock_movie):
        self._post(_event("playback.start", MOVIE_DATA))
        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)

    @patch("app.providers.tmdb.movie", return_value=MOVIE_METADATA)
    def test_movie_finished_marks_completed(self, _mock_movie):
        self._post(_event("playback.start", MOVIE_DATA))
        self._post(_event("playback.finished", MOVIE_DATA))
        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    @patch("app.providers.tmdb.movie", return_value=MOVIE_METADATA)
    def test_playback_stop_does_not_reopen_finished_movie(self, _mock_movie):
        self._post(_event("playback.finished", MOVIE_DATA))
        self._post(_event("playback.stop", MOVIE_DATA))
        movies = Movie.objects.filter(item__media_id="603", user=self.user)
        self.assertEqual(movies.count(), 1)
        self.assertEqual(movies.first().status, Status.COMPLETED.value)

    @patch("app.providers.tmdb.movie")
    def test_missing_tmdb_id_is_ignored(self, mock_movie):
        self._post(_event("playback.finished", {**MOVIE_DATA, "tmdbId": 0}))
        mock_movie.assert_not_called()
        self.assertFalse(Movie.objects.exists())

    @patch("app.providers.tmdb.movie")
    def test_test_event_is_ignored(self, mock_movie):
        response = self._post(_event("test", {"message": "Hello from BingeBandit"}))
        self.assertEqual(response.status_code, 200)
        mock_movie.assert_not_called()

    # Live playback state looks up show artwork too; keep it out of the count
    @patch("app.live_playback.apply_playback_event")
    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    def test_episode_uses_show_tmdb_id(self, mock_tv, mock_handle_episode, _mock_live):
        mock_tv.return_value = {
            "media_id": "1668",
            "title": "Friends",
            "tvdb_id": None,
            "season/1": {"episodes": [{"episode_number": 1}]},
        }
        self._post(_event("playback.finished", EPISODE_DATA))
        mock_tv.assert_called_once_with("1668", [1])
        args = mock_handle_episode.call_args.args
        self.assertEqual(args[:3], ("1668", 1, 1))

    @patch("app.live_playback.apply_playback_event")
    def test_player_events_update_live_playback(self, mock_apply):
        self._post(_event("player.pause", {**EPISODE_DATA, "sessionId": "abc"}))
        kwargs = mock_apply.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "media.pause")
        self.assertEqual(kwargs["playback_media_type"], MediaTypes.EPISODE.value)
        self.assertEqual(kwargs["media_id"], "1668")
        self.assertEqual(kwargs["series_title"], "Friends")
        self.assertEqual(kwargs["episode_number"], 1)
        self.assertEqual(kwargs["view_offset_seconds"], 1300)
        self.assertEqual(kwargs["duration_seconds"], 1320)

    @patch("app.providers.tmdb.movie")
    @patch("app.live_playback.apply_playback_event")
    def test_player_events_do_not_track(self, _mock_apply, mock_movie):
        self._post(_event("player.play", MOVIE_DATA))
        mock_movie.assert_not_called()
        self.assertFalse(Movie.objects.exists())
