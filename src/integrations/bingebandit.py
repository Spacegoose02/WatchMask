"""Thin client for the BingeBandit HTTP API.

https://github.com/Spacegoose02/BingeBandit/blob/main/docs/API.md
"""

import csv
import io

import requests

LIBRARY_PAGE_SIZE = 500


class BingeBanditError(Exception):
    """BingeBandit request failed."""


class BingeBanditAuthError(BingeBanditError):
    """BingeBandit rejected the API key."""


class BingeBanditClient:
    """Talk to a BingeBandit server with a user's API key."""

    def __init__(self, base_url: str, api_key: str):
        """Initialize with the server URL and a `bb_` API key."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _get(self, path, params=None):
        try:
            response = requests.get(
                f"{self.base_url}/api{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            msg = f"Could not reach BingeBandit: {exc}"
            raise BingeBanditError(msg) from exc
        if response.status_code in (401, 403):
            msg = "BingeBandit API key is invalid or was revoked."
            raise BingeBanditAuthError(msg)
        if not response.ok:
            msg = f"BingeBandit request failed ({response.status_code}) for {path}"
            raise BingeBanditError(msg)
        return response

    def get_me(self):
        """Return the user the API key belongs to."""
        return self._get("/me").json()

    def get_libraries(self):
        """Return the libraries the user can see as [{id, name, kind}]."""
        return [
            {"id": lib["id"], "name": lib.get("name", ""), "kind": lib.get("kind", "")}
            for lib in self._get("/libraries").json()
        ]

    def get_library_tmdb_ids(self, library_id):
        """Return the TMDB IDs of the movies and shows in a library."""
        tmdb_ids = set()
        offset = 0
        while True:
            page = self._get(
                f"/libraries/{library_id}/items",
                {"offset": offset, "limit": LIBRARY_PAGE_SIZE},
            ).json()
            items = page.get("items") or []
            tmdb_ids.update(
                str(item["tmdbId"])
                for item in items
                if item.get("kind") in ("movie", "show") and item.get("tmdbId")
            )
            offset += len(items)
            if not items or offset >= page.get("total", 0):
                return tmdb_ids

    def get_history(self):
        """Return the user's plays from the history CSV export, newest first.

        Unlike /me/history, the export carries TMDB IDs (the show's for
        episodes) and season/episode numbers.
        """
        response = self._get("/me/export/history.csv")
        response.encoding = "utf-8"
        return list(csv.DictReader(io.StringIO(response.text)))
