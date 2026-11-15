"""Sign in with BingeBandit: show a code, wait for its approval, finish."""

import logging

import requests
from allauth.account.internal.decorators import login_not_required
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.helpers import (
    complete_social_login,
    render_authentication_error,
)
from allauth.socialaccount.internal import statekit
from allauth.socialaccount.providers.base.views import BaseLoginView
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import urlencode

from integrations.bingebandit_signin.provider import BingeBanditProvider

logger = logging.getLogger(__name__)

DEVICE_NAME = "Watchmask sign-in"
LINK_KEY = "bingebandit_link:{state}"
USER_KEY = "bingebandit_user:{state}"
HTTP_ACCEPTED = 202
HTTP_GONE = 410


class BingeBanditSignInError(Exception):
    """BingeBandit could not be reached or refused the request."""


def _provider(request):
    return get_adapter().get_provider(request, BingeBanditProvider.id)


def _server(provider):
    return provider.app.settings.get("server_url", "").rstrip("/")


def _start_link(server):
    """Ask BingeBandit for a code to approve; return its poll token and link."""
    try:
        response = requests.post(
            f"{server}/api/link/start",
            json={"device": DEVICE_NAME},
            timeout=10,
        )
    except requests.RequestException as exc:
        msg = f"Could not reach BingeBandit: {exc}"
        raise BingeBanditSignInError(msg) from exc
    if not response.ok:
        msg = f"BingeBandit would not start a sign-in ({response.status_code})"
        raise BingeBanditSignInError(msg)
    data = response.json()
    return {
        "poll_token": data["pollToken"],
        "code": data["code"],
        "link_url": data.get("linkUrl") or f"{server}/link?code={data['code']}",
    }


def _poll_link(server, poll_token):
    """Return BingeBandit's user once the code is approved, else a status."""
    response = requests.get(
        f"{server}/api/link/poll",
        params={"token": poll_token},
        timeout=10,
    )
    if response.status_code == HTTP_ACCEPTED:
        return "waiting", None
    if response.status_code == HTTP_GONE:
        return "expired", None
    response.raise_for_status()
    data = response.json()
    # The approval hands over a full BingeBandit session. Watchmask only
    # needed to learn who approved it, so end that session straight away.
    try:
        requests.post(
            f"{server}/api/auth/logout",
            headers={"Authorization": f"Bearer {data['token']}"},
            timeout=5,
        )
    except requests.RequestException:
        logger.warning("Could not end the BingeBandit session made for sign-in")
    return "approved", data["user"]


@method_decorator(login_not_required, name="dispatch")
class LoginView(BaseLoginView):
    """Start a sign-in or a link from a provider button."""

    provider_id = BingeBanditProvider.id


login = LoginView.as_view()


@login_not_required
def wait(request):
    """Show the code to approve on BingeBandit and wait for it."""
    provider = _provider(request)
    state = request.GET.get("state", "")
    key = LINK_KEY.format(state=state)
    if state not in request.session.get(statekit.STATES_SESSION_KEY, {}):
        return render_authentication_error(request, provider=provider)
    link = request.session.get(key)
    if link is None or request.GET.get("new"):
        try:
            link = _start_link(_server(provider))
        except (BingeBanditSignInError, KeyError, ValueError) as exc:
            return render_authentication_error(
                request, provider=provider, exception=exc
            )
        request.session[key] = link
    return render(
        request,
        "bingebandit_signin/wait.html",
        {
            "code": link["code"],
            "link_url": link["link_url"],
            "poll_url": f"{reverse('bingebandit_poll')}?{urlencode({'state': state})}",
            "new_code_url": (
                f"{reverse('bingebandit_wait')}?{urlencode({'state': state, 'new': 1})}"
            ),
        },
    )


@login_not_required
def poll(request):
    """Report whether the code was approved; the page polls this."""
    provider = _provider(request)
    state = request.GET.get("state", "")
    link = request.session.get(LINK_KEY.format(state=state))
    if not link:
        return JsonResponse({"status": "expired"})
    try:
        status, user = _poll_link(_server(provider), link["poll_token"])
    except (requests.RequestException, KeyError, ValueError):
        logger.warning("BingeBandit sign-in poll failed", exc_info=True)
        return JsonResponse({"status": "waiting"})
    if status != "approved":
        if status == "expired":
            request.session.pop(LINK_KEY.format(state=state), None)
        return JsonResponse({"status": status})
    request.session.pop(LINK_KEY.format(state=state), None)
    request.session[USER_KEY.format(state=state)] = user
    return JsonResponse(
        {
            "status": "approved",
            "next": f"{reverse('bingebandit_callback')}?{urlencode({'state': state})}",
        },
    )


@login_not_required
def callback(request):
    """Finish signing in (or linking) as the BingeBandit user who approved."""
    provider = _provider(request)
    state = request.GET.get("state", "")
    user = request.session.pop(USER_KEY.format(state=state), None)
    if not user:
        return render_authentication_error(request, provider=provider)
    sociallogin = provider.sociallogin_from_response(request, user)
    sociallogin.state = provider.unstash_redirect_state(request, state)
    return complete_social_login(request, sociallogin)
