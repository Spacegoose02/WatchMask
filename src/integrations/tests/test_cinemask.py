from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from app.models import Item, Movie, Status
from integrations import cinemask
from integrations.imports import helpers
from integrations.models import CinemaskAccount, CinemaskRequest
from integrations.webhooks.cinemask import CinemaskWebhookProcessor
from users.models import User

METADATA = {"title": "Paddington", "image": "https://example.com/p.jpg"}


def _response(status=200, json_data=None):
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = json_data
    return response


def _row(request_id=12, tmdb_id=116149, status="pending", kind="movie"):
    return {
        "id": request_id,
        "kind": kind,
        "tmdbId": tmdb_id,
        "title": "Paddington",
        "status": status,
    }


class CinemaskTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="john", password="pw")
        self.user.token = "x" * 32
        self.user.save(update_fields=["token"])
        self.account = CinemaskAccount.objects.create(
            user=self.user,
            base_url="https://cinemask.test",
            api_key=helpers.encrypt("cmk_key"),
        )
        self.client.force_login(self.user)


@patch("app.providers.services.get_media_metadata", return_value=METADATA)
@patch("integrations.cinemask.requests.request")
class CinemaskSyncTests(CinemaskTestCase):
    def test_sync_saves_requests_and_adds_new_ones_as_planning(
        self, mock_request, _meta
    ):
        mock_request.return_value = _response(
            json_data=[_row(), _row(13, 1399, "declined", "show")]
        )

        cinemask.sync_requests(self.account)

        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("GET", "https://cinemask.test/api/requests"))
        self.assertEqual(kwargs["headers"], {"X-Api-Key": "cmk_key"})
        self.assertEqual(CinemaskRequest.objects.count(), 2)
        self.assertEqual(
            CinemaskRequest.objects.get(request_id=13).media_type,
            "tv",
        )
        # Declined requests are not added.
        self.assertEqual(Movie.objects.get().status, Status.PLANNING.value)
        self.assertEqual(Item.objects.count(), 1)
        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.last_sync_at)

    def test_sync_updates_status_and_prunes_missing(self, mock_request, _meta):
        CinemaskRequest.objects.create(
            user=self.user, request_id=12, media_type="movie", tmdb_id="116149"
        )
        CinemaskRequest.objects.create(
            user=self.user, request_id=99, media_type="movie", tmdb_id="1"
        )
        mock_request.return_value = _response(json_data=[_row(status="available")])

        cinemask.sync_requests(self.account)

        self.assertEqual(
            list(CinemaskRequest.objects.values_list("request_id", "status")),
            [(12, "available")],
        )
        # Existing requests aren't re-added to tracking.
        self.assertEqual(Movie.objects.count(), 0)

    def test_sync_marks_connection_broken_on_auth_error(self, mock_request, _meta):
        mock_request.return_value = _response(
            status=401, json_data={"error": "bad key"}
        )

        with self.assertRaises(cinemask.CinemaskAuthError):
            cinemask.sync_requests(self.account)

        self.account.refresh_from_db()
        self.assertTrue(self.account.connection_broken)


@patch("app.providers.services.get_media_metadata", return_value=METADATA)
@patch("integrations.cinemask.requests.request")
class CinemaskRequestViewTests(CinemaskTestCase):
    def test_request_sends_to_cinemask_and_shows_status(self, mock_request, _meta):
        mock_request.return_value = _response(json_data=_row())

        response = self.client.post(
            reverse("cinemask_request"),
            {"media_type": "movie", "tmdb_id": "116149"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Requested")
        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", "https://cinemask.test/api/requests"))
        self.assertEqual(kwargs["json"], {"kind": "movie", "tmdbId": 116149})
        self.assertTrue(CinemaskRequest.objects.filter(request_id=12).exists())
        self.assertEqual(Movie.objects.get().status, Status.PLANNING.value)

    def test_request_error_shows_retry(self, mock_request, _meta):
        mock_request.return_value = _response(
            status=409,
            json_data={"error": "This is already on the server"},
        )

        response = self.client.post(
            reverse("cinemask_request"),
            {"media_type": "movie", "tmdb_id": "116149"},
        )

        self.assertContains(response, "Retry request")
        self.assertContains(response, "This is already on the server")
        self.assertFalse(CinemaskRequest.objects.exists())

    def test_button_shows_request_for_untracked_and_status_for_requested(
        self, mock_request, _meta
    ):
        url = reverse("cinemask_request_button")
        self.assertContains(
            self.client.get(url, {"media_type": "tv", "tmdb_id": "1399"}), "Request"
        )

        CinemaskRequest.objects.create(
            user=self.user,
            request_id=5,
            media_type="tv",
            tmdb_id="1399",
            status="approved",
        )
        self.assertContains(
            self.client.get(url, {"media_type": "tv", "tmdb_id": "1399"}), "Approved"
        )
        mock_request.assert_not_called()

    def test_button_is_empty_without_account(self, mock_request, _meta):
        self.account.delete()
        response = self.client.get(
            reverse("cinemask_request_button"),
            {"media_type": "movie", "tmdb_id": "1"},
        )
        self.assertEqual(response.content, b"")


class CinemaskWebhookTests(CinemaskTestCase):
    def _payload(self, event="request.available", requested_by="kid", request_id=12):
        return {
            "source": "cinemask",
            "event": event,
            "data": {
                "kind": "movie",
                "tmdbId": 116149,
                "requestId": request_id,
                "requestedBy": requested_by,
            },
        }

    def test_endpoint_rejects_bad_token(self):
        response = self.client.post(
            reverse("cinemask_webhook", kwargs={"token": "bad"}),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @patch("integrations.views.tasks.process_webhook.delay")
    def test_endpoint_queues_payload(self, mock_delay):
        payload = self._payload()
        response = self.client.post(
            reverse("cinemask_webhook", kwargs={"token": self.user.token}),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with("cinemask", payload, self.user.id)

    @patch("app.providers.services.get_media_metadata", return_value=METADATA)
    def test_available_updates_own_request_and_adds_to_tracking(self, _meta):
        CinemaskRequest.objects.create(
            user=self.user, request_id=12, media_type="movie", tmdb_id="116149"
        )
        self.account.allowed_usernames = "someone-else"
        self.account.save()

        CinemaskWebhookProcessor().process_payload(self._payload(), self.user)

        self.assertEqual(CinemaskRequest.objects.get().status, "available")
        self.assertEqual(Movie.objects.get().status, Status.PLANNING.value)

    @patch("app.providers.services.get_media_metadata", return_value=METADATA)
    def test_allowlist_blocks_other_requesters(self, _meta):
        self.account.allowed_usernames = "partner"
        self.account.save()

        CinemaskWebhookProcessor().process_payload(
            self._payload(requested_by="kid"), self.user
        )
        self.assertEqual(Movie.objects.count(), 0)

        CinemaskWebhookProcessor().process_payload(
            self._payload(requested_by="Partner"), self.user
        )
        self.assertEqual(Movie.objects.count(), 1)

    @patch("app.providers.services.get_media_metadata", return_value=METADATA)
    def test_add_to_tracking_off_skips_adding(self, _meta):
        self.account.add_to_tracking = False
        self.account.save()

        CinemaskWebhookProcessor().process_payload(self._payload(), self.user)
        self.assertEqual(Movie.objects.count(), 0)


@patch("integrations.views.tasks.sync_cinemask_requests.delay")
@patch("integrations.cinemask.requests.request")
class CinemaskConnectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="jane", password="pw")
        self.client.force_login(self.user)

    def test_connect_stores_encrypted_key_and_queues_sync(
        self, mock_request, mock_delay
    ):
        mock_request.return_value = _response(json_data=[])

        self.client.post(
            reverse("cinemask_connect"),
            {"base_url": "https://cinemask.test/", "api_key": "cmk_key"},
        )

        account = CinemaskAccount.objects.get(user=self.user)
        self.assertNotEqual(account.api_key, "cmk_key")
        self.assertEqual(helpers.decrypt(account.api_key), "cmk_key")
        mock_delay.assert_called_once_with(user_id=self.user.id)

    def test_connect_rejects_bad_key(self, mock_request, mock_delay):
        mock_request.return_value = _response(status=401, json_data={"error": "nope"})

        self.client.post(
            reverse("cinemask_connect"),
            {"base_url": "https://cinemask.test", "api_key": "bad"},
        )

        self.assertFalse(CinemaskAccount.objects.exists())
        mock_delay.assert_not_called()


class CinemaskSettingsPageTests(CinemaskTestCase):
    def test_integrations_page_shows_connection_and_webhook(self):
        response = self.client.get(reverse("integrations"))
        self.assertContains(response, "Connected to")
        self.assertContains(response, f"/webhook/cinemask/{self.user.token}")

    def test_settings_save(self):
        self.client.post(
            reverse("cinemask_settings"),
            {"allowed_usernames": " kid , partner ,"},
        )
        self.account.refresh_from_db()
        self.assertFalse(self.account.add_to_tracking)
        self.assertEqual(self.account.allowed_usernames, "kid,partner")
