"""Seasons folded into TV, and the BingeBandit library filter."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources, Status
from integrations.models import BingeBanditLibraryItem
from lists.models import CustomList, CustomListItem
from lists.views_helpers import _get_completed_item_ids


@override_settings(WATCHMASK_HIDDEN_MEDIA_TYPES=("boardgame", "season"))
class HiddenSeasonsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="u", password="12345")
        self.client.login(username="u", password="12345")

    def test_seasons_stay_active_under_tv(self):
        self.assertNotIn(MediaTypes.SEASON.value, self.user.get_enabled_media_types())
        self.assertIn(MediaTypes.SEASON.value, self.user.get_active_media_types())

    def test_season_list_redirects_to_tv_when_hidden(self):
        response = self.client.get(reverse("medialist", args=[MediaTypes.SEASON.value]))
        self.assertRedirects(
            response,
            reverse("medialist", args=[MediaTypes.TV.value]),
            fetch_redirect_response=False,
        )


class LibraryFilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="u", password="12345")
        self.client.login(username="u", password="12345")
        self._movie("1", "Kids Film")
        self._movie("2", "Grown-up Film")
        for tmdb_id, item_id, library in (
            ("1", 10, "Kids Movies"),
            ("1", 10, "Movies"),
            ("2", 11, "Movies"),
        ):
            BingeBanditLibraryItem.objects.create(
                user=self.user,
                media_type="movie",
                tmdb_id=tmdb_id,
                item_id=item_id,
                library_name=library,
            )

    def _movie(self, tmdb_id, title):
        item = Item.objects.create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/x.jpg",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.COMPLETED.value, progress=1)

    def _titles(self, **params):
        response = self.client.get(reverse("medialist", args=[MediaTypes.MOVIE.value]), params)
        self.assertEqual(response.status_code, 200)
        titles = sorted(m.item.title for m in response.context["media_list"].object_list)
        return response, titles

    def test_library_chips_and_filter(self):
        response, titles = self._titles()
        self.assertEqual(response.context["library_options"], ["Kids Movies", "Movies"])
        self.assertEqual(titles, ["Grown-up Film", "Kids Film"])
        self.assertContains(response, "All libraries")

        _, titles = self._titles(library="Kids Movies")
        self.assertEqual(titles, ["Kids Film"])

    def test_unknown_library_is_ignored(self):
        response, titles = self._titles(library="Nope")
        self.assertEqual(response.context["current_library"], "")
        self.assertEqual(titles, ["Grown-up Film", "Kids Film"])


class ListCompletionTests(TestCase):
    def test_completed_ids_from_subquery(self):
        user = get_user_model().objects.create_user(username="u", password="12345")
        custom_list = CustomList.objects.create(name="L", owner=user)
        done = Item.objects.create(
            media_id="1", source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, title="Done",
        )
        todo = Item.objects.create(
            media_id="2", source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, title="Todo",
        )
        Movie.objects.create(item=done, user=user, status=Status.COMPLETED.value, progress=1)
        Movie.objects.create(item=todo, user=user, status=Status.PLANNING.value, progress=0)
        CustomListItem.objects.create(custom_list=custom_list, item=done)
        CustomListItem.objects.create(custom_list=custom_list, item=todo)

        completed = _get_completed_item_ids(
            user,
            CustomListItem.objects.filter(custom_list=custom_list).values("item_id"),
        )
        self.assertEqual(completed, {done.id})
