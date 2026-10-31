import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="Sync BingeBandit library")
def sync_bingebandit_library(user_id):
    """Refresh which movies and shows one user can play on BingeBandit."""
    from integrations import bingebandit
    from integrations.models import BingeBanditAccount

    account = (
        BingeBanditAccount.objects.select_related("user").filter(user_id=user_id).first()
    )
    if not account:
        return "No BingeBandit account"
    count = bingebandit.sync_library(account)
    return f"Synced {count} BingeBandit library items"


@shared_task(name="Sync BingeBandit libraries for all users")
def sync_all_bingebandit_libraries():
    """Refresh the BingeBandit library index for every connected account."""
    from integrations import bingebandit
    from integrations.models import BingeBanditAccount

    synced = errors = 0
    for account in BingeBanditAccount.objects.select_related("user"):
        try:
            bingebandit.sync_library(account)
            synced += 1
        except bingebandit.BingeBanditError as exc:
            errors += 1
            logger.warning(
                "BingeBandit library sync failed for %s: %s",
                account.user.username,
                exc,
            )
    return {"synced": synced, "errors": errors}
