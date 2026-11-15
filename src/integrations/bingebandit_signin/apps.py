from django.apps import AppConfig


class BingeBanditSignInConfig(AppConfig):
    """allauth finds the provider in this app's ``provider`` module."""

    name = "integrations.bingebandit_signin"
    label = "bingebandit_signin"
    verbose_name = "BingeBandit sign-in"
