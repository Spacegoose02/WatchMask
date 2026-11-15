from unittest.mock import MagicMock, patch

from allauth.socialaccount.models import SocialAccount, SocialApp
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from integrations.imports.helpers import decrypt
from integrations.models import BingeBanditAccount
from users import sso

User = get_user_model()
BB_URL = "https://bb.example.com"


def _response(status, data=None):
    response = MagicMock(status_code=status, ok=200 <= status < 300)
    response.json.return_value = data or {}
    return response


class ProviderSettingsTests(TestCase):
    """Settings > Advanced: add, edit and remove sign-in providers."""

    def setUp(self):
        """Sign in as a superuser."""
        self.admin = User.objects.create_superuser(username="admin", password="pw")
        self.client.force_login(self.admin)

    def _add_oidc(self, **overrides):
        data = {
            "kind": "openid_connect",
            "name": "Tinyauth",
            "issuer": "https://auth.example.com/",
            "client_id": "cid",
            "secret": "s3cret",
            "scopes": "groups",
            "show_on_login": "1",
            **overrides,
        }
        return self.client.post(reverse("add_sso_provider"), data)

    def test_add_oidc_provider_stores_encrypted_secret(self):
        """The dialog makes an OpenID Connect app with an encrypted secret."""
        self._add_oidc()
        app = SocialApp.objects.get()
        self.assertEqual(app.provider, "openid_connect")
        self.assertEqual(app.provider_id, "tinyauth")
        self.assertEqual(app.settings["server_url"], "https://auth.example.com")
        self.assertEqual(
            app.settings["scope"], ["openid", "profile", "email", "groups"]
        )
        self.assertFalse(app.settings["auto_create"])
        self.assertTrue(app.settings["show_on_login"])
        self.assertNotEqual(app.secret, "s3cret")
        self.assertEqual(decrypt(app.secret), "s3cret")
        listed = sso.SocialAccountAdapter().list_apps(None)
        self.assertEqual(listed[0].secret, "s3cret")

    def test_edit_without_secret_keeps_it(self):
        """Leaving the secret empty while editing keeps the saved one."""
        self._add_oidc()
        app = SocialApp.objects.get()
        self.client.post(
            reverse("edit_sso_provider", args=[app.pk]),
            {
                "name": "Auth",
                "issuer": "https://auth.example.com",
                "client_id": "cid",
                "auto_create": "1",
            },
        )
        app.refresh_from_db()
        self.assertEqual(decrypt(app.secret), "s3cret")
        self.assertEqual(app.name, "Auth")
        self.assertEqual(app.provider_id, "tinyauth")
        self.assertTrue(app.settings["auto_create"])
        self.assertFalse(app.settings["show_on_login"])

    def test_delete_removes_links(self):
        """Removing a provider also removes the accounts linked through it."""
        self._add_oidc()
        app = SocialApp.objects.get()
        SocialAccount.objects.create(user=self.admin, provider="tinyauth", uid="1")
        self.client.post(reverse("delete_sso_provider", args=[app.pk]))
        self.assertFalse(SocialApp.objects.exists())
        self.assertFalse(SocialAccount.objects.exists())

    def test_only_superusers_can_manage_providers(self):
        """Other users get a 403."""
        self.client.force_login(User.objects.create_user(username="u", password="pw"))
        self.assertEqual(self._add_oidc().status_code, 403)
        self.assertFalse(SocialApp.objects.exists())

    def test_advanced_page_lists_provider_with_callback(self):
        """The provider and its redirect URL show on the Advanced page."""
        self._add_oidc()
        response = self.client.get(reverse("advanced"))
        self.assertContains(response, "Sign-in Providers")
        self.assertContains(response, "/accounts/oidc/tinyauth/login/callback/")

    def test_one_bingebandit_provider(self):
        """BingeBandit sign-in can be set up once."""
        data = {"kind": "bingebandit", "name": "BingeBandit", "issuer": BB_URL}
        self.client.post(reverse("add_sso_provider"), data)
        self.client.post(reverse("add_sso_provider"), data)
        app = SocialApp.objects.get()
        self.assertEqual(app.provider, "bingebandit")
        self.assertEqual(app.provider_id, "")


class SignInPageTests(TestCase):
    """The sign-in page shows only providers set to appear there."""

    def test_hidden_provider_stays_linkable(self):
        """A hidden provider is off the sign-in page but on the linking page."""
        sso.save_provider(
            SocialApp(),
            kind=sso.OIDC,
            name="Hidden IdP",
            issuer="https://h.example.com",
            client_id="c",
            secret="s",
            show=False,
        )
        sso.save_provider(
            SocialApp(),
            kind=sso.OIDC,
            name="Shown IdP",
            issuer="https://s.example.com",
            client_id="c",
            secret="s",
            show=True,
        )
        login_page = self.client.get(reverse("account_login"))
        self.assertContains(login_page, "Shown IdP")
        self.assertNotContains(login_page, "Hidden IdP")
        self.client.force_login(User.objects.create_user(username="u", password="pw"))
        connections = self.client.get(reverse("socialaccount_connections"))
        self.assertContains(connections, "Hidden IdP")


@patch("integrations.bingebandit_signin.views.requests")
class BingeBanditSignInTests(TestCase):
    """Sign in by approving a code on BingeBandit."""

    def setUp(self):
        """Set up BingeBandit sign-in."""
        self.app = sso.save_provider(
            SocialApp(),
            kind=sso.BINGEBANDIT,
            name="BingeBandit",
            issuer=BB_URL,
        )

    def _sign_in(self, requests_mock, bb_user):
        requests_mock.RequestException = Exception
        requests_mock.post.return_value = _response(
            200,
            {
                "code": "ABC123",
                "pollToken": "tok",
                "linkUrl": f"{BB_URL}/link?code=ABC123",
            },
        )
        start = self.client.post(reverse("bingebandit_login"), {"process": "login"})
        self.assertEqual(start.status_code, 302)
        wait = self.client.get(start.url)
        self.assertContains(wait, "ABC123")
        self.assertContains(wait, f"{BB_URL}/link?code=ABC123")
        poll_url = wait.context["poll_url"]

        requests_mock.get.return_value = _response(202)
        self.assertEqual(self.client.get(poll_url).json(), {"status": "waiting"})

        requests_mock.get.return_value = _response(
            200, {"token": "sess", "user": bb_user}
        )
        approved = self.client.get(poll_url).json()
        self.assertEqual(approved["status"], "approved")
        # The borrowed BingeBandit session is ended at once.
        requests_mock.post.assert_called_with(
            f"{BB_URL}/api/auth/logout",
            headers={"Authorization": "Bearer sess"},
            timeout=5,
        )
        return self.client.get(approved["next"])

    def _signed_in_user(self):
        return User.objects.filter(pk=self.client.session.get("_auth_user_id")).first()

    def test_linked_account_signs_in(self, requests_mock):
        """A Watchmask user linked to the BingeBandit user is signed in."""
        user = User.objects.create_user(username="sam", password="pw")
        SocialAccount.objects.create(user=user, provider="bingebandit", uid="7")
        self._sign_in(requests_mock, {"id": 7, "username": "sammy"})
        self.assertEqual(self._signed_in_user(), user)

    def test_connected_integration_links_on_first_sign_in(self, requests_mock):
        """Whoever connected that BingeBandit user's API key is linked."""
        user = User.objects.create_user(username="sam", password="pw")
        BingeBanditAccount.objects.create(
            user=user,
            base_url=f"{BB_URL}/",
            api_key="x",
            username="Sammy",
        )
        self._sign_in(requests_mock, {"id": 7, "username": "sammy"})
        self.assertEqual(self._signed_in_user(), user)
        self.assertTrue(
            SocialAccount.objects.filter(
                user=user, provider="bingebandit", uid="7"
            ).exists(),
        )

    def test_connection_on_another_server_does_not_link(self, requests_mock):
        """The same username on a different BingeBandit server is someone else."""
        user = User.objects.create_user(username="sam", password="pw")
        BingeBanditAccount.objects.create(
            user=user,
            base_url="https://other.example.com",
            api_key="x",
            username="sammy",
        )
        response = self._sign_in(requests_mock, {"id": 7, "username": "sammy"})
        self.assertIsNone(self._signed_in_user())
        self.assertRedirects(
            response, reverse("account_login"), fetch_redirect_response=False
        )
        self.assertIn(
            "No Watchmask account is linked",
            " ".join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_auto_create_makes_an_account(self, requests_mock):
        """With account creation on, a new BingeBandit user gets an account."""
        self.app.settings["auto_create"] = True
        self.app.save()
        self._sign_in(requests_mock, {"id": 9, "username": "newbie"})
        user = self._signed_in_user()
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "newbie")

    def test_signed_in_user_links_bingebandit(self, requests_mock):
        """Linking from Settings > Account attaches the BingeBandit user."""
        user = User.objects.create_user(username="sam", password="pw")
        self.client.force_login(user)
        requests_mock.RequestException = Exception
        requests_mock.post.return_value = _response(
            200,
            {"code": "C", "pollToken": "t", "linkUrl": f"{BB_URL}/link?code=C"},
        )
        start = self.client.post(reverse("bingebandit_login"), {"process": "connect"})
        wait = self.client.get(start.url)
        requests_mock.get.return_value = _response(
            200,
            {"token": "s", "user": {"id": 3, "username": "sam-bb"}},
        )
        approved = self.client.get(wait.context["poll_url"]).json()
        self.client.get(approved["next"])
        self.assertTrue(
            SocialAccount.objects.filter(
                user=user, provider="bingebandit", uid="3"
            ).exists(),
        )

    def test_expired_code(self, requests_mock):
        """An expired code is reported so the page can offer a new one."""
        requests_mock.RequestException = Exception
        requests_mock.post.return_value = _response(
            200,
            {"code": "C", "pollToken": "t", "linkUrl": f"{BB_URL}/link?code=C"},
        )
        start = self.client.post(reverse("bingebandit_login"), {"process": "login"})
        wait = self.client.get(start.url)
        requests_mock.get.return_value = _response(410)
        self.assertEqual(
            self.client.get(wait.context["poll_url"]).json(), {"status": "expired"}
        )

    def test_wait_page_needs_a_started_sign_in(self, requests_mock):
        """A made-up state does not start a code on BingeBandit."""
        response = self.client.get(reverse("bingebandit_wait"), {"state": "nope"})
        self.assertEqual(response.status_code, 401)
        requests_mock.post.assert_not_called()
