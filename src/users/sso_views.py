"""Settings > Advanced: add, edit and remove sign-in providers."""

from urllib.parse import urlparse

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from users import sso


def provider_rows(request):
    """Return the providers for the Advanced page, each with its callback URL."""
    return [
        {
            "app": app,
            "callback_url": request.build_absolute_uri(
                reverse(
                    "openid_connect_callback",
                    kwargs={"provider_id": app.provider_id},
                ),
            )
            if app.provider == sso.OIDC
            else "",
        }
        for app in sso.managed_providers()
    ]


def bingebandit_default_url(request):
    """Prefill BingeBandit's address from the admin's own connection."""
    account = getattr(request.user, "bingebandit_account", None)
    return account.base_url if account else ""


@require_POST
def save_sso_provider(request, pk=None):
    """Create or update a sign-in provider from the dialog."""
    if not request.user.is_superuser:
        return HttpResponse(status=403)
    app = get_object_or_404(sso.managed_providers(), pk=pk) if pk else sso.SocialApp()
    post = request.POST
    kind = app.provider if pk else post.get("kind", sso.OIDC)
    if kind not in (sso.OIDC, sso.BINGEBANDIT):
        return HttpResponse(status=400)
    name = post.get("name", "").strip()[:40] or (
        "BingeBandit" if kind == sso.BINGEBANDIT else ""
    )
    issuer = post.get("issuer", "").strip().rstrip("/")
    client_id = post.get("client_id", "").strip()
    secret = post.get("secret", "").strip()
    parsed = urlparse(issuer)
    if not name or parsed.scheme not in ("http", "https") or not parsed.netloc:
        messages.error(request, "Give the provider a name and a full https:// URL.")
        return redirect(f"{reverse('advanced')}#sign-in-providers")
    if kind == sso.OIDC and (not client_id or (not pk and not secret)):
        messages.error(request, "Enter the client ID and secret the provider gave you.")
        return redirect(f"{reverse('advanced')}#sign-in-providers")
    if (
        kind == sso.BINGEBANDIT
        and sso.SocialApp.objects.filter(provider=sso.BINGEBANDIT)
        .exclude(pk=app.pk)
        .exists()
    ):
        messages.error(request, "BingeBandit sign-in is already set up; edit that one.")
        return redirect(f"{reverse('advanced')}#sign-in-providers")
    sso.save_provider(
        app,
        kind=kind,
        name=name,
        issuer=issuer,
        client_id=client_id,
        secret=secret,
        scopes=post.get("scopes", ""),
        auto_create=post.get("auto_create") == "1",
        show=post.get("show_on_login") == "1",
    )
    messages.success(request, f"Saved the {name} sign-in.")
    return redirect(f"{reverse('advanced')}#sign-in-providers")


@require_POST
def delete_sso_provider(request, pk):
    """Remove a sign-in provider and the accounts linked through it."""
    if not request.user.is_superuser:
        return HttpResponse(status=403)
    app = get_object_or_404(sso.managed_providers(), pk=pk)
    sso.SocialAccount.objects.filter(provider=sso.account_provider(app)).delete()
    app.delete()
    messages.success(request, f"Removed the {app.name} sign-in.")
    return redirect(f"{reverse('advanced')}#sign-in-providers")
