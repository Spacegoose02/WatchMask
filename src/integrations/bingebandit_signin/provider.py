"""BingeBandit as an allauth provider.

BingeBandit has no OAuth server, so this reuses the code it shows on TVs:
Watchmask asks BingeBandit for a code, the person approves it on BingeBandit
(where they're already signed in), and Watchmask's next poll gets back the
BingeBandit user. The provider's one ``SocialApp`` holds the server URL in
``settings["server_url"]``.
"""

from allauth.socialaccount.providers.base import Provider, ProviderAccount
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import urlencode


class BingeBanditAccount(ProviderAccount):
    """A linked BingeBandit user."""

    def to_str(self):
        """Show the BingeBandit username on the linked sign-ins page."""
        return self.account.extra_data.get("username") or super().to_str()


class BingeBanditProvider(Provider):
    """Sign in by approving a code on BingeBandit."""

    id = "bingebandit"
    name = "BingeBandit"
    account_class = BingeBanditAccount
    supports_redirect = True

    def __init__(self, request, app=None):
        """Label the button with the name the admin gave it."""
        super().__init__(request, app=app)
        if app is not None and app.name:
            self.name = app.name

    def get_login_url(self, request, **kwargs):
        """Return the URL that starts a sign-in or link."""
        url = reverse("bingebandit_login")
        if kwargs:
            url = f"{url}?{urlencode(kwargs)}"
        return url

    def redirect(self, request, process, next_url=None, data=None, **kwargs):
        """Send the browser to the page that shows the code."""
        state = self.stash_redirect_state(request, process, next_url, data, **kwargs)
        return HttpResponseRedirect(
            f"{reverse('bingebandit_wait')}?{urlencode({'state': state})}",
        )

    def extract_uid(self, data):
        """Return the BingeBandit user id."""
        return str(data["id"])

    def extract_common_fields(self, data):
        """Use the BingeBandit username for a new Watchmask account."""
        return {"username": data.get("username", "")}


provider_classes = [BingeBanditProvider]
