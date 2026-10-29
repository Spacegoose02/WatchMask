"""BingeBandit watch history importer."""

import logging

from simple_history.utils import bulk_update_with_history

import app
from integrations import bingebandit as bingebandit_api
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError
from integrations.imports.trakt import TraktImporter

logger = logging.getLogger(__name__)


def importer(library, user, mode):
    """Import the connected BingeBandit account's finished plays."""
    account = getattr(user, "bingebandit_account", None)
    if not account:
        msg = "BingeBandit is not connected for this user."
        raise MediaImportError(msg)
    client = bingebandit_api.BingeBanditClient(
        account.base_url,
        helpers.decrypt(account.api_key),
    )
    return BingeBanditImporter(client, user, mode, library).import_data()


class BingeBanditImporter(TraktImporter):
    """Replay BingeBandit's history export through the Trakt history logic.

    Both give a TMDB ID per play (the show's for episodes) plus season and
    episode numbers and a watch time, so rows are reshaped into Trakt history
    entries and the movie/episode handling is reused as is.
    """

    def __init__(self, client, user, mode, library="all"):
        """Initialize with an API client and an optional library ID."""
        super().__init__(client.base_url, user, mode)
        self.client = client
        self.library = library or "all"

    def import_data(self):
        """Import finished movie and episode plays."""
        try:
            rows = self.client.get_history()
            library_tmdb_ids = (
                None
                if self.library == "all"
                else self.client.get_library_tmdb_ids(self.library)
            )
        except bingebandit_api.BingeBanditError as exc:
            raise MediaImportError(str(exc)) from exc

        # The export is newest first; replay oldest first like Trakt does.
        for row in reversed(rows):
            if row.get("completed") != "true":
                continue
            if library_tmdb_ids is not None and row.get("tmdb_id") not in library_tmdb_ids:
                continue
            try:
                self._process_row(row)
            except MediaImportError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("Skipping BingeBandit history row")
                self.warnings.append(
                    f"{row.get('title') or 'Unknown title'}: skipped a watch entry "
                    f"due to an unexpected error ({e}).",
                )

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        if self.completed_seasons:
            bulk_update_with_history(self.completed_seasons, app.models.Season, fields=["status"])
        if self.completed_tvs:
            bulk_update_with_history(self.completed_tvs, app.models.TV, fields=["status"])

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        return imported_counts, "\n".join(dict.fromkeys(self.warnings))

    def _process_row(self, row):
        title = row.get("title") or ""
        if not row.get("tmdb_id"):
            self.warnings.append(f"{title}: No TMDB ID found.")
            return
        media = {"title": title, "ids": {"tmdb": row["tmdb_id"]}}
        entry = {"watched_at": row.get("watched_at") or ""}
        if row.get("type") == "movie":
            self.process_watched_movie({**entry, "movie": media})
        elif row.get("type") == "episode":
            self.process_watched_episode(
                {
                    **entry,
                    "show": media,
                    "episode": {
                        "season": int(row["season"]),
                        "number": int(row["episode"]),
                    },
                },
            )
