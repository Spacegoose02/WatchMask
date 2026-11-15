"""Sign-in providers that an admin manages in Settings > Advanced.

Each one is an allauth ``SocialApp``: an OpenID Connect provider (Tinyauth,
Authelia, Pocket ID, ...) or the one "Sign in with BingeBandit" provider.
Watchmask keeps its own flags in ``SocialApp.settings``:

- ``managed``: made in Settings, so the flags below apply.
- ``auto_create``: anyone the provider vouches for gets a new account on
  first sign-in. Off means the person signs in with their password once and
  links the provider under Settings > Account.
- ``show_on_login``: show the provider's button on the sign-in page. A hidden
  provider can still be linked from Settings > Account.

Providers configured through the ``SOCIALACCOUNT_PROVIDERS`` environment
variable keep working as before.
"""

import re
from urllib.parse import urlparse

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount, SocialApp
from cryptography.fernet import InvalidToken
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from integrations.imports import helpers

OIDC = "openid_connect"
BINGEBANDIT = "bingebandit"

__all__ = ["BINGEBANDIT", "OIDC", "SocialAccount", "SocialApp"]


def managed_providers():
    """Return the sign-in providers made in Settings, oldest first."""
    return SocialApp.objects.filter(
        provider__in=(OIDC, BINGEBANDIT),
        settings__managed=True,
    ).order_by("pk")


def account_provider(app):
    """Return the value ``SocialAccount.provider`` has for ``app``'s links."""
    return app.provider_id or app.provider


def unique_provider_id(name, exclude_pk=None):
    """Return a URL-safe provider id for ``name`` that no other app uses."""
    from allauth.socialaccount.adapter import get_adapter

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sso"
    taken = {
        account_provider(app)
        for app in get_adapter().list_apps(None)
        if app.pk is None or app.pk != exclude_pk
    }
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def save_provider(
    app,
    *,
    kind,
    name,
    issuer,
    client_id="",
    secret="",
    scopes="",
    auto_create=False,
    show=True,
):
    """Fill ``app`` from the dialog's fields and save it."""
    app.provider = kind
    app.name = name
    settings = dict(app.settings or {})
    settings.update(
        managed=True,
        server_url=issuer,
        auto_create=auto_create,
        show_on_login=show,
    )
    if kind == BINGEBANDIT:
        # One BingeBandit server; its links are stored as "bingebandit".
        app.client_id = BINGEBANDIT
        app.provider_id = ""
    else:
        app.client_id = client_id
        if not app.provider_id:
            app.provider_id = unique_provider_id(name, exclude_pk=app.pk)
        extra = [s for s in re.split(r"[\s,]+", scopes) if s]
        if extra:
            settings["scope"] = list(
                dict.fromkeys(["openid", "profile", "email", *extra]),
            )
        else:
            settings.pop("scope", None)
        if secret:
            app.secret = helpers.encrypt(secret)
            settings["secret_encrypted"] = True
    app.settings = settings
    app.save()
    return app


def _bingebandit_connection_owner(app, username):
    """Return the one user who connected this BingeBandit user's API key.

    Connecting BingeBandit under Settings > Integrations already proved who
    they are on that server, so their first BingeBandit sign-in links to it.
    """
    from integrations.models import BingeBanditAccount

    host = urlparse(app.settings.get("server_url", "")).netloc.lower()
    if not username or not host:
        return None
    owners = [
        account.user
        for account in BingeBanditAccount.objects.filter(
            username__iexact=username,
        ).select_related("user")
        if urlparse(account.base_url).netloc.lower() == host
    ]
    return owners[0] if len(owners) == 1 else None


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Apply the per-provider settings made in Settings > Advanced."""

    def list_apps(self, request, provider=None, client_id=None):
        """Return apps with stored secrets decrypted for use."""
        apps = super().list_apps(request, provider=provider, client_id=client_id)
        for app in apps:
            if app.pk and app.settings.get("secret_encrypted") and app.secret:
                try:
                    app.secret = helpers.decrypt(app.secret)
                except InvalidToken:
                    # SECRET changed since it was saved; the provider will
                    # refuse the sign-in and the admin re-enters the secret.
                    app.secret = ""
                app.settings = {**app.settings, "secret_encrypted": False}
        return apps

    def pre_social_login(self, request, sociallogin):
        """Link a first BingeBandit sign-in to the account that connected it."""
        app = getattr(sociallogin.provider, "app", None)
        if (
            sociallogin.is_existing
            or request.user.is_authenticated
            or not app
            or app.provider != BINGEBANDIT
        ):
            return
        user = _bingebandit_connection_owner(
            app,
            sociallogin.account.extra_data.get("username", ""),
        )
        if user and user.is_active:
            sociallogin.connect(request, user)

    def is_open_for_signup(self, request, sociallogin):
        """Create accounts only for providers that allow it.

        A sign-in from someone with no linked account goes back to the sign-in
        page with a note on how to link it, instead of a dead end.
        """
        app = getattr(sociallogin.provider, "app", None)
        if not (app and app.settings.get("managed")):
            return super().is_open_for_signup(request, sociallogin)
        if app.settings.get("auto_create"):
            return True
        messages.error(
            request,
            _(
                "No Watchmask account is linked to that %(provider)s sign-in yet. "
                "Sign in with your password, then link it under Settings > Account."
            )
            % {"provider": app.name},
        )
        raise ImmediateHttpResponse(redirect("account_login"))
