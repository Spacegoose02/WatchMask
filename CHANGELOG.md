# Changelog

Releases are tagged with date-based versions (`vYY.M.D`). Each tag builds and
publishes `ghcr.io/spacegoose02/watchmask` with the matching version tags plus
`:release`.

## v26.11.2

### New

- **BingeBandit library chips**: library sync keeps each server library
  separately, so the Movies and TV lists get a chip per library (for example
  Movies, Kids Movies, TV Shows, Kids TV Shows).
- **Smart list `library` rule**: pick BingeBandit libraries with chips in the
  list editor. A library list shows everything in that library, watched or not.
- **BingeBandit collection**: titles on the server are marked collected and
  un-collected when they're removed. New titles are matched on TMDB, 500 per
  sync.
- **Request on BingeBandit**: a button on movie and show pages that aren't on
  the server.
- Optional push of completions back to BingeBandit, and an optional webhook
  secret that checks `X-BingeBandit-Signature`.
- **LootDen**: connect with a personal API token from the Import page (Games).
  Games sync every hour with play time summed across devices; hours only go
  up, and statuses you set by hand are kept. Matched game pages get a
  "Play on LootDen" button.

### Performance

- List pages compute the breakdown and completion only on a full page load,
  using a subquery for completion.
- Smart-list sync is chunked, and bulk collection syncs skip per-item signal
  work. The Plex collection sync is locked and rebuilds caches once.

### Upgrade notes

- Runs three new database migrations (`integrations.0065` to `0067`). They are
  safe to re-run.
- Library chips appear after the next BingeBandit library sync.

## v26.11.1 — Rebased on Floppy

Watchmask now sits on top of current [Floppy](https://github.com/dannyvfilms/Floppy)
(`latest` at 928bc40, 31 Oct 2026) instead of the 10 July snapshot it was
forked from. That brings in about 2,300 Floppy commits, including:

- A shared library-query engine that pages the media list, API, smart lists,
  list pages and Home shelves in SQL.
- New covering indexes (items by media type, Media by user and dates,
  episodes by season and date, Discover candidates, calendar events).
- SQLite tuning: per-connection cache and mmap, `IMMEDIATE` transactions,
  daily `PRAGMA optimize`, and bounded background verification at startup.
- Lists that no longer load every item to render menus or pages, and smart
  list rules evaluated in the background.
- Home renders only a warm first group and loads the rest per row.
- Statistics kept warm by a durable background sync.
- Redis cache compression and single-flight cache rebuilds.
- Bounded Celery memory, staged large imports, and many new integrations and
  sync paths (incremental watched/progress sync API, rating sync-back, MAL
  sync, Komga, Kavita, Hardcover, Kapowarr, Mylar3 and more).

### Watchmask changes carried over

- Cinemask requests and the BingeBandit integration (webhook tracking,
  history import, Play/Resume buttons, library badges, Continue Watching).
- Watchmask name and raccoon logo, docker image
  `ghcr.io/spacegoose02/watchmask`.
- HTMX polling pauses in hidden tabs; background Celery runs under `nice`.
- Board games and the standalone TV seasons section are switched off
  by default. Each user can turn them back on under Settings.

### Upgrade notes

- Back up your database first. This release runs Floppy's migrations from
  July to October (several hundred, including index builds), which can take
  a while on a large SQLite database.
- Watchmask's own integrations migrations keep their names and are joined to
  Floppy's chain by `integrations.0064_merge_watchmask_integrations`, so an
  existing Watchmask database upgrades in place.
- The background web process count and memory defaults now follow Floppy's
  host-size tiers; see `docs/deployment.md` if you had tuned
  `WEB_CONCURRENCY` or `GUNICORN_THREADS`.

## v26.10.31

### New

- **BingeBandit Play and Resume**: connected users' BingeBandit libraries are
  indexed on connect and every 6 hours. Detail pages show a Play button that
  opens the title in BingeBandit, and media cards show a badge when a title is
  in the library.
- **Continue Watching**: `playback.stop` webhooks save where a movie or episode
  stopped part way. Home lists these under Continue Watching, and the detail
  page button becomes Resume, opening BingeBandit's player at that position.

### Performance

- The Postgres connection pool now matches the gunicorn thread count, so
  requests no longer queue for a free connection, and workers no longer inherit
  the master's pool.
- The IMDB dataset refresh runs at most once a day, 10 minutes after startup,
  instead of on every restart.
- Celery workers and beat run at lower CPU priority so web requests win when
  the server is busy.
- HTMX polling pauses while the tab is hidden, and the service worker skips
  non-static requests.
- The live BingeBandit status check times out after 5 seconds.
- The nginx access log records request and upstream timings.

### Upgrade notes

- Runs one new database migration
  (`integrations.0018_bingebandit_library_resume`) on start.
- To see Play buttons and badges for an existing BingeBandit connection, wait
  for the first library index (on connect, then every 6 hours).

## v26.10.29

### New

- **BingeBandit import card**: connect a BingeBandit server from the Import
  page with an API key, pick a library, and import finished movie and episode
  plays from its history export. Imports can be scheduled like Plex's. The
  usernames saved on the card also filter the webhook, alongside `?users=`.
- The BingeBandit webhook now says what's wrong when it receives the
  Plex-style format, instead of a bare 400: set the webhook's Format to
  BingeBandit JSON.

### Fixes

- Completing a show or season no longer crashes when the provider payload is
  missing seasons, episodes or progress, and a provider error (an ID TMDB no
  longer knows, or an outage) during an import or webhook keeps the saved
  record instead of failing.
- The TV details page no longer crashes on TMDB responses without a score.
- Bulk play dates keep the chosen time of day, air-date bulk saves skip
  episodes without an air date, and music plays spread evenly when release
  dates are missing.
- The music origin filter shows country names again.
- Lint now passes in CI, and many outdated tests were brought up to date with
  the current code.

### Upgrade notes

- Runs one new database migration (`integrations.0017_bingebanditaccount`) on
  start.

## v26.10.28 — First Watchmask release

### Rebrand

- Yamtrack is now **Watchmask**, with a new raccoon logo in the sidebar, public
  header and sign-in screen, and a new favicon and PWA icon set.
- User-facing text uses the new name: page titles, the About page,
  notifications, the PWA manifest, export filenames, the ICS/TOTP issuer and
  outbound User-Agent strings.
- Internal identifiers are unchanged (`YAMTRACK_*` environment variables, the
  `/yamtrack` container paths, database names, URL names, the RSS namespace and
  import source keys), so existing data, imports and configuration keep
  working.
- The Docker image moves to `ghcr.io/spacegoose02/watchmask`. To switch an
  existing install, change the `image:` line and keep your volumes and
  environment as they are.

### New integrations

- **BingeBandit**: track progress from BingeBandit's JSON webhooks.
  `playback.start` marks items in progress, `playback.finished` marks them
  watched, and `player.*` events drive the Now Playing card. An optional
  `?users=` filter on the webhook URL limits tracking to chosen BingeBandit
  users.
- **Cinemask**: connect a Cinemask server with a full-access API key to request
  movies and shows from their detail pages, see each request's status, and add
  requests to your library as Planning. Statuses refresh every 30 minutes and
  instantly through Cinemask's `request.created` / `request.available`
  webhook, with a requester allowlist like the Jellyseerr integration.

### Upgrade notes

- Runs one new database migration (`integrations.0016_cinemask`) on start.
- This release is based on the July 2026 Yamtrack fork code. Do not point an
  install that already runs a newer upstream image (`ghcr.io/dannyvfilms/yamtrack`
  from mid-July 2026 onward) at this image: its database has migrations this
  release does not know about.
