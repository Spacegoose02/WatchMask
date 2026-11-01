"""Mirror completions in Watchmask to the user's BingeBandit server."""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from app.models import TV, MediaTypes, Movie, Sources, Status
from integrations.models import BingeBanditAccount


@receiver(post_save, sender=Movie)
@receiver(post_save, sender=TV)
def push_completion_to_bingebandit(sender, instance, created, **kwargs):
    """Queue a watched push when a movie or show becomes Completed."""
    from app.signals import media_change_side_effects_suppressed

    if kwargs.get("raw") or media_change_side_effects_suppressed():
        return
    if instance.status != Status.COMPLETED.value:
        return
    if not created and not instance.tracker.has_changed("status"):
        return
    item = instance.item
    if item.source != Sources.TMDB.value or item.media_type not in (
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
    ):
        return
    if not BingeBanditAccount.objects.filter(
        user_id=instance.user_id,
        push_watched=True,
    ).exists():
        return

    from integrations.tasks import push_bingebandit_watched

    user_id, media_type, tmdb_id = instance.user_id, item.media_type, item.media_id
    transaction.on_commit(
        lambda: push_bingebandit_watched.delay(user_id, media_type, tmdb_id),
    )
