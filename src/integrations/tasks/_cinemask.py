import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="Sync Cinemask requests")
def sync_cinemask_requests(user_id):
    """Refresh one user's Cinemask request statuses."""
    from integrations import cinemask
    from integrations.models import CinemaskAccount

    account = (
        CinemaskAccount.objects.select_related("user").filter(user_id=user_id).first()
    )
    if not account:
        return "No Cinemask account"
    count = cinemask.sync_requests(account)
    return f"Synced {count} Cinemask requests"


@shared_task(name="Sync Cinemask requests for all users")
def sync_all_cinemask_requests():
    """Refresh request statuses for every connected Cinemask account."""
    from integrations import cinemask
    from integrations.models import CinemaskAccount

    synced = errors = 0
    accounts = CinemaskAccount.objects.filter(connection_broken=False).select_related(
        "user"
    )
    for account in accounts:
        try:
            cinemask.sync_requests(account)
            synced += 1
        except cinemask.CinemaskError as exc:
            errors += 1
            logger.warning(
                "Cinemask sync failed for %s: %s", account.user.username, exc
            )
    return {"synced": synced, "errors": errors}
