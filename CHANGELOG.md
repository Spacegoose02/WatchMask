# Changelog

Releases are tagged with date-based versions (`vYY.M.D`). Each tag builds and
publishes `ghcr.io/spacegoose02/watchmask` with the matching version tags plus
`:release`.

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
