import logging

from integrations import cinemask
from integrations.models import CinemaskAccount, CinemaskRequest, CinemaskRequestStatus

logger = logging.getLogger(__name__)


class CinemaskWebhookProcessor:
    """
    Processor for Cinemask webhook events (Settings > API and webhooks).

    Payload shape:
      {"source": "cinemask", "event": "request.created" | "request.available",
       "data": {"kind": "movie" | "show", "tmdbId": 123, "requestId": 12,
                "requestedBy": "kid"}}

    Cinemask sends one webhook for the whole server, so requests from other
    people are only added when they pass the requester allowlist.
    """

    STATUS_BY_EVENT = {
        "request.created": CinemaskRequestStatus.PENDING,
        "request.available": CinemaskRequestStatus.AVAILABLE,
    }

    def process_payload(self, payload, user):
        try:
            account = user.cinemask_account
        except CinemaskAccount.DoesNotExist:
            logger.debug(
                "Cinemask webhook ignored: user %s has no Cinemask account",
                user.username,
            )
            return

        event = payload.get("event")
        if event not in self.STATUS_BY_EVENT:
            logger.debug("Cinemask webhook ignored: event %r", event)
            return

        data = payload.get("data") or {}
        media_type = cinemask.MEDIA_TYPE_BY_KIND.get(data.get("kind"))
        tmdb_id = data.get("tmdbId")
        if not media_type or not tmdb_id:
            logger.warning(
                "Cinemask webhook ignored: unsupported kind=%r tmdbId=%r",
                data.get("kind"),
                tmdb_id,
            )
            return

        # Only the user's own requests are stored; those came from a sync or
        # from Yamtrack, so they already exist.
        own_request = CinemaskRequest.objects.filter(
            user=user,
            request_id=data.get("requestId"),
        ).first()
        if own_request:
            new_status = self.STATUS_BY_EVENT[event]
            if new_status == CinemaskRequestStatus.AVAILABLE:
                own_request.status = new_status
                own_request.save(update_fields=["status", "updated_at"])

        if not account.add_to_tracking:
            return
        if not own_request and not self._requester_allowed(account, data):
            logger.debug(
                "Cinemask webhook ignored: requester %r not allowed",
                data.get("requestedBy"),
            )
            return

        logger.info(
            "Cinemask accepted: user=%s event=%s type=%s tmdb=%s",
            user.username,
            event,
            media_type,
            tmdb_id,
        )
        cinemask.add_to_tracking(user, media_type, tmdb_id)

    @staticmethod
    def _requester_allowed(account, data):
        """Empty allowlist accepts everyone, like Jellyseerr."""
        allowed = {
            name.strip().lower()
            for name in account.allowed_usernames.split(",")
            if name.strip()
        }
        if not allowed:
            return True
        requester = (data.get("requestedBy") or "").strip().lower()
        return requester in allowed
