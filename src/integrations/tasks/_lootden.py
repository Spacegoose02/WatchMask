import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="Sync LootDen games")
def sync_lootden(user_id):
    """Pull one user's LootDen games and play time."""
    from integrations import lootden
    from integrations.models import LootDenAccount

    account = LootDenAccount.objects.select_related("user").filter(user_id=user_id).first()
    if not account:
        return "No LootDen account"
    try:
        counts = lootden.sync(account)
    except lootden.LootDenError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error"])
        raise
    return counts


@shared_task(name="Sync LootDen games for all users")
def sync_all_lootden():
    """Pull LootDen play time for every connected account."""
    from integrations import lootden
    from integrations.models import LootDenAccount

    synced = errors = 0
    for account in LootDenAccount.objects.select_related("user"):
        try:
            lootden.sync(account)
            synced += 1
        except lootden.LootDenError as exc:
            errors += 1
            account.last_error = str(exc)
            account.save(update_fields=["last_error"])
            logger.warning("LootDen sync failed for %s: %s", account.user.username, exc)
    return {"synced": synced, "errors": errors}
