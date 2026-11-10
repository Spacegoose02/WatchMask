from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources
from integrations import bingebandit
from integrations.imports import helpers
from integrations.models import BingeBanditAccount, BingeBanditLibraryItem
from lists.models import CustomList


def _item(tmdb_id, media_type=MediaTypes.MOVIE.value):
    return Item.objects.create(
        media_id=str(tmdb_id),
        source=Sources.TMDB.value,
        media_type=media_type,
        title=f"Title {tmdb_id}",
        image="http://example.com/poster.jpg",
    )


class FakeServer:
    """Answer the BingeBandit list endpoints from in-memory data."""

    def __init__(self, watchlist, playlists):
        """Hold the watchlist entries and {id: (name, items)} playlists."""
        self.watchlist = watchlist
        self.playlists = playlists

    def __call__(self, _client, path, params=None, timeout=30):
        server = self

        class Response:
            def json(self):
                if path == "/watchlist":
                    return server.watchlist
                if path == "/playlists":
                    return [
                        {"id": pid, "name": name}
                        for pid, (name, _) in server.playlists.items()
                    ]
                pid = int(path.rsplit("/", 1)[1])
                name, items = server.playlists[pid]
                return {"playlist": {"id": pid, "name": name}, "items": items}

        return Response()


class BingeBanditListSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test", password="12345"
        )
        self.account = BingeBanditAccount.objects.create(
            user=self.user,
            base_url="http://bb.local",
            api_key=helpers.encrypt("bb_key"),
            username="alice",
            sync_collection=False,
            sync_lists=True,
        )
        self.matrix = _item(603)
        self.inception = _item(27205)
        self.breaking_bad = _item(1396, MediaTypes.TV.value)
        # The show's BingeBandit id, so its episodes map back to it.
        BingeBanditLibraryItem.objects.create(
            user=self.user,
            media_type=MediaTypes.TV.value,
            tmdb_id="1396",
            item_id=50,
        )

    def _sync(self, server):
        # A plain function, so it binds as a method like the real _get.
        def fake_get(client, path, params=None, timeout=30):
            return server(client, path, params, timeout)

        with patch("integrations.bingebandit.BingeBanditClient._get", fake_get):
            return bingebandit.sync_lists(self.account)

    def _titles(self, source_id):
        custom_list = CustomList.objects.get(
            owner=self.user,
            source="bingebandit",
            source_id=source_id,
        )
        return list(
            custom_list.customlistitem_set.order_by("list_item_id").values_list(
                "item__media_id",
                flat=True,
            )
        )

    def test_watchlist_and_playlists_become_lists(self):
        server = FakeServer(
            # Newest first, as BingeBandit sends it.
            watchlist=[
                {"id": 2, "kind": "movie", "tmdbId": 27205},
                {"id": 1, "kind": "movie", "tmdbId": 603},
            ],
            playlists={
                7: (
                    "Friday night",
                    [
                        {"id": 1, "kind": "movie", "tmdbId": 603},
                        {"id": 51, "kind": "episode", "tmdbId": 62085, "showId": 50},
                        {"id": 52, "kind": "episode", "tmdbId": 62086, "showId": 50},
                        {"id": 99, "kind": "movie", "tmdbId": 0},
                    ],
                ),
            },
        )

        self.assertEqual(self._sync(server), (4, 0))

        self.assertEqual(self._titles("watchlist"), ["603", "27205"])
        self.assertEqual(self._titles("7"), ["603", "1396"])
        self.assertEqual(
            CustomList.objects.get(source_id="watchlist").name,
            bingebandit.WATCHLIST_NAME,
        )
        playlist = CustomList.objects.get(source_id="7")
        self.assertEqual(playlist.name, "Friday night")
        self.assertEqual(playlist.visibility, "private")

    def test_changes_on_bingebandit_are_mirrored(self):
        server = FakeServer(
            watchlist=[{"id": 1, "kind": "movie", "tmdbId": 603}],
            playlists={7: ("Old name", [{"id": 1, "kind": "movie", "tmdbId": 603}])},
        )
        self._sync(server)
        watchlist = CustomList.objects.get(source_id="watchlist")

        server.watchlist = [{"id": 2, "kind": "movie", "tmdbId": 27205}]
        server.playlists = {
            8: ("New one", [{"id": 20, "kind": "show", "tmdbId": 1396}])
        }
        self.assertEqual(self._sync(server), (2, 1))

        # Same list, new contents; the deleted playlist's list is gone.
        self.assertEqual(CustomList.objects.get(source_id="watchlist").pk, watchlist.pk)
        self.assertEqual(self._titles("watchlist"), ["27205"])
        self.assertFalse(CustomList.objects.filter(source_id="7").exists())
        self.assertEqual(self._titles("8"), ["1396"])

    def test_playlist_rename_follows(self):
        server = FakeServer(watchlist=[], playlists={7: ("Old name", [])})
        self._sync(server)
        server.playlists = {7: ("New name", [])}
        self._sync(server)
        self.assertEqual(CustomList.objects.get(source_id="7").name, "New name")

    def test_other_lists_are_left_alone(self):
        mine = CustomList.objects.create(name="Mine", owner=self.user)
        mine.items.add(self.matrix)
        self._sync(FakeServer(watchlist=[], playlists={}))
        self.assertTrue(mine.items.filter(pk=self.matrix.pk).exists())

    @patch("integrations.tasks.sync_bingebandit_lists.delay")
    def test_turning_on_in_settings_syncs_at_once(self, delay):
        self.account.sync_lists = False
        self.account.save()
        self.client.login(username="test", password="12345")

        self.client.post(reverse("bingebandit_settings"), {"sync_lists": "on"})

        self.account.refresh_from_db()
        self.assertTrue(self.account.sync_lists)
        delay.assert_called_once_with(user_id=self.user.id)

    def test_scheduled_sync_skips_accounts_with_it_off(self):
        from integrations.tasks import sync_all_bingebandit_lists

        self.account.sync_lists = False
        self.account.save()
        with patch("integrations.bingebandit.sync_lists") as sync:
            self.assertEqual(sync_all_bingebandit_lists(), {"synced": 0, "errors": 0})
        sync.assert_not_called()
