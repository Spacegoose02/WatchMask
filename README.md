<p align="center">
  <img alt="Watchmask" src="src/static/img/floppy-logo@2x.png" width="317" />
</p>

<p align="center">
  <b>Your own private Trakt, Letterboxd, and Goodreads — in one app, on your own hardware.</b><br>
  Track movies, TV, anime, books, comics, manga and games in one place, with BingeBandit built in.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="https://github.com/dannyvfilms/Floppy/wiki">Docs (Floppy wiki)</a> ·
  <a href="https://github.com/Spacegoose02/Yamtrack/releases">Releases</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

Watchmask is a self-hosted media tracker and diary for everything you watch, read,
play, and listen to. Log
what you finish, see what to pick up next, and get a yearly recap of how you spent
your time. Connect Plex, Jellyfin, Emby, Pocket Casts, or Last.fm and your history
fills itself in.

It is free and open source. You run it on a computer or server at
home and open it from any browser, on desktop or phone. Your data never leaves
your machine unless you connect a service that needs it.

<p align="center">
  <img alt="A movie's detail page in Watchmask, with backdrop, ratings, and check-in button" src="docs/screenshots/readme/details.jpg" width="880" />
</p>

## A look around

<table>
  <tr>
    <td width="50%" valign="top">
      <img alt="Home screen with In Progress and Planning rows" src="docs/screenshots/readme/home.jpg" /><br>
      <b>Home</b> — what you're in the middle of, what's next, and rows you choose.
    </td>
    <td width="50%" valign="top">
      <img alt="Statistics page with hours, streaks, and charts" src="docs/screenshots/readme/statistics.jpg" /><br>
      <b>Statistics</b> — a recap of your year, by media type, day, and hour.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img alt="Lists page with shareable list covers" src="docs/screenshots/readme/lists.jpg" /><br>
      <b>Lists</b> — private, public, or smart lists that fill themselves.
    </td>
    <td valign="top">
      <img alt="Collection page of owned media" src="docs/screenshots/readme/collection.jpg" /><br>
      <b>Collection</b> — what you own, down to the format and resolution.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img alt="Release calendar for tracked shows and games" src="docs/screenshots/readme/calendar.jpg" /><br>
      <b>Calendar</b> — upcoming episodes and releases for everything you track.
    </td>
    <td valign="top">
      <img alt="Watchmask on a phone: home, a movie, and statistics" src="docs/screenshots/readme/mobile.jpg" /><br>
      <b>On your phone</b> — built for mobile, and installable as an app.
    </td>
  </tr>
</table>

Light and dark themes follow your device; switch with the moon icon or under
**Settings → Appearance**.

## What it does

- **One library for every kind of media.** Movies and TV through to music
  albums, podcast episodes, board games, and comics — each with pages, progress,
  and ratings that fit it.
- **Knows what's next.** In-progress shows show the next episode and time
  left; the calendar and optional notifications tell you when new ones arrive.
- **History that fills itself in.** Plays from Plex, Jellyfin, Emby, Pocket
  Casts, and Last.fm are logged automatically. Manual check-ins take one tap.
- **Recaps worth reading.** Hours by media type, streaks, top people, and
  year-over-year comparisons.
- **Lists you can share.** Public pages, RSS feeds, and smart lists built
  from rules.
- **Your collection, too.** Track physical and digital copies alongside what
  you've watched or read, with Plex collection sync.
- **Recommendations from your own taste.** Discover rows learn from what you
  rate and finish, and you can send requests to Seerr in one click.
- **Bring your history with you.** Import from Trakt, TV Time, Simkl, IMDb,
  MyAnimeList, AniList, Goodreads, StoryGraph, Steam, PlayStation, Xbox, and
  more. Export
  everything to CSV at any time.

The [full feature tour](https://github.com/dannyvfilms/Floppy/wiki/Features) on
the wiki lists everything, including multi-user accounts, OIDC sign-in, a REST
API, and an MCP server.

## Try it

The [live demo](https://yamtrack.dannyvfilms.com) is open — sign in with
username `demo` and password `demodemo`. It's one shared account, so what
you add is visible to everyone else trying it.

## Install

Watchmask runs in Docker. Use the [Docker Compose example](docs/deployment.md#docker-compose-recommended)
with the `ghcr.io/spacegoose02/watchmask` image; `SECRET` is the only setting you must fill in.
[Docker Run](docs/deployment.md#docker-run) and the [deployment reference](docs/deployment.md)
cover PostgreSQL, reverse proxies, environment variables and upgrades.

Once it is running, open it in your phone's browser and add it to your home screen.

## Privacy and trust

Your library, ratings, and activity stay in the database you run. Watchmask has no
analytics and no central update check. It does contact metadata providers (such
as TMDB) to look up titles and artwork, and connected services receive what their
features need. Run it for yourself or people you trust, and turn off sign-ups
after creating your account. Details:
[security notes](docs/security-hardening.md) ·
[reporting a vulnerability](SECURITY.md).

## Watchmask, Watchmask and Yamtrack

Watchmask is built on [Floppy](https://github.com/dannyvfilms/Floppy), which
started as a fork of [Yamtrack](https://github.com/FuzzyGrim/Yamtrack). It
follows Watchmask closely and adds a native [BingeBandit](https://github.com/Spacegoose02/BingeBandit)
integration (webhook tracking, history import, Play/Resume and Continue
Watching) and Cinemask requests. Existing Watchmask and Yamtrack data keep
working; Yamtrack CSV exports import under **Settings → Import**.

## Building on Watchmask

Watchmask has a REST API at `/api/v1` and an [MCP server](mcp_server/) for AI
assistants. Start with the
[API and MCP guide](https://github.com/dannyvfilms/Floppy/wiki/7.-API-and-MCP-Server);
your running instance serves its own API index at `/api/docs/`. Download tools
can read lists and report owned media ([FlexGet guide](docs/integrations/flexget.md)).

## Support

Report Watchmask bugs on [this repository's issues](https://github.com/Spacegoose02/Yamtrack/issues).
Thank you to everyone who has contributed to Watchmask, and to Yamtrack before it.

## Configuration and deployment

The full operator reference moved to [docs/deployment.md](docs/deployment.md).
Links into the old sections of this page land here:

<a name="floppy-cant-reach-redis"></a><a name="sqlite-data-paths"></a><a name="postgresql"></a><a name="environment-variables"></a><a name="running-on-a-small-host"></a><a name="upgrading-container-images"></a><a name="verifying-a-published-image-actually-has-a-fix"></a><a name="reverse-proxy-setup"></a><a name="docker-image-tags"></a>

- [Docker Compose example](docs/deployment.md#docker-compose-recommended) ·
  [Docker Run](docs/deployment.md#docker-run) ·
  [Portainer and Unraid](docs/deployment.md#portainer-and-unraid)
- [SQLite data paths](docs/deployment.md#sqlite-data-paths) ·
  [SQLite startup and migration preparation](docs/architecture/sqlite-startup.md) ·
  [PostgreSQL](docs/deployment.md#postgresql) ·
  [Persistence checklist](docs/deployment.md#persistence-checklist)
- [Environment variables](docs/deployment.md#environment-variables) ·
  [Running on a small host](docs/deployment.md#running-on-a-small-host) ·
  [Reverse proxy setup](docs/deployment.md#reverse-proxy-setup)
- [Watchmask can't reach Redis](docs/deployment.md#floppy-cant-reach-redis) ·
  [Startup diagnostics](docs/deployment.md#startup-diagnostics) ·
  [I updated and my login is gone](docs/deployment.md#troubleshooting-i-updated-and-my-login-is-gone)
- [Upgrading container images](docs/deployment.md#upgrading-container-images) ·
  [Verifying a published image](docs/deployment.md#verifying-a-published-image-actually-has-a-fix) ·
  [Docker image tags](docs/deployment.md#docker-image-tags)
- Developing locally: [contributing guide](CONTRIBUTING.md#local-development)

## License

AGPL-3.0.

## Origins and AI use

Watchmask began as a fork of [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack)
and still shares its foundation and data model; this repository keeps the fork
link. It has since grown into its own project. Thanks to FuzzyGrim and Yamtrack's
contributors for the groundwork.

Watchmask uses AI coding agents for implementation, tests, and documentation under
maintainer direction. This describes Watchmask's development, not all inherited
Yamtrack history. PRs should disclose the model/workflow used and record human
review only when it actually occurred. Agent reviews and passing checks are
not independent security certification.
