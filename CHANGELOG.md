# Changelog

All notable changes to this project are documented here, generated from
[Conventional Commits](https://www.conventionalcommits.org) via [git-cliff](https://git-cliff.org).
Add a release with: `git cliff --unreleased --tag vX.Y.Z --prepend CHANGELOG.md` (see CONTRIBUTING.md).
Versions before v0.5.0 are kept manually and must not be regenerated.
## v1.4.1 (2026-11-12)

### Fixed
- Read the infohashes before the arr tool forgets them

## v1.4.0 (2026-11-12)

### Added
- Make a bulk deletion readable and tickable in one gesture


### Fixed
- Tell an unknown infohash apart from a torrent that is really gone

## v1.3.0 (2026-11-12)

### Added
- Read the signing key from a file when SECRET_KEY_FILE is set


### Fixed
- Neutralise the alpine/git entrypoint in the mirror job

## v1.2.3 (2026-11-12)

### Fixed
- Bump PyJWT to 2.15.1, which 2.14.0 was not enough to reach

## v1.2.2 (2026-11-12)

### Fixed
- Bump PyJWT to 2.14.0

## v1.2.1 (2026-11-12)

### Fixed
- Stop reporting a sync success that never happened

## v1.2.0 (2026-11-11)

### Added
- Review a deletion item by item before confirming it
- Say during the connection test whether deletions will be possible
- Add a dry run to the manual delete route
- Ask the media server to forget the files the arr tool removed


### CI/CD
- Mirror the source to GitHub on every main push


### Changed
- One deletion service, and an outcome per target instead of a verdict
- Remove the demo instance and its read-only mode


### Fixed
- Stop reporting freed space the hardlinks did not actually free
- Match torrents by infohash instead of title when purging

## v1.1.3 (2026-08-10)

### Fixed
- Keep failure messages readable in the modal and the history

## v1.1.2 (2026-08-10)

### Documentation
- Document media server deletion and the matching change


### Fixed
- Explain why a media server deletion failed

## v1.1.1 (2026-08-10)

### Fixed
- Replace python-jose with PyJWT

## v1.1.0 (2026-08-10)

### Added
- Offer media server deletion as a delete target
- Support deleting media through the server API


### CI/CD
- Run the pytest suite on every pipeline


### Documentation
- Fix changelog regeneration workflow and doc inconsistencies


### Fixed
- Alert when the scheduled sync fails
- Report skipped deletions instead of claiming success
- Match arr items by external id before falling back to title
- Send the query parameters the API accepts
- Correct git-cliff archive extraction path in release job


## v1.0.0 (2026-06-26)

### Added
- Store and display both arr_path and media server file_path separately

### Fixed
- Encode non-ASCII ntfy notification titles as RFC 2047
- Bump python-multipart to 0.0.32 (CVE-2026-53538/53539/53540)
- Use Sonarr/Radarr path as primary file_path source
- Add file_path to Jellyfin get_series() — was only fetched for movies

### CI/CD
- Disable Docker layer cache to prevent stale code in builds

### Chores
- Add git-cliff config, regenerate CHANGELOG from commits, auto-generate in CI

---

## v0.5.0 (2026-05-13)

### Added
- Display filesystem path in media detail panel
- Rate limiting, unit tests, TMDB ID fallback, bug fix
- Initial public release

### Fixed
- Resolution mismatch on files with embedded cover art; add FS path to duplicates
- Jellyfin admin API key 400 error + rules buttons overlap
- Dashboard Delete button onclick broken by tojson double quotes
- Bump app.js cache-buster to v7 to force browser reload
- Series file sizes now aggregated from episode-level data
- Dashboard Delete button now removes stale rows after successful deletion
- Prevent duplicate library items from multiple Plex sections
- Post-publish audit — 8 logic and correctness issues
- Test connection reads form values before DB
- Redirect to dashboard after login instead of library

### Changed
- Centralise SECRET_KEY in config.py, improve migrate_db error logging
- Replace action badges with clean dot+label list
- Redesign rules card layout for clarity and consistency

### Documentation
- Add CHANGELOG, CONTRIBUTING and inline code comments
- Add screenshot gallery and redesign README
- Add FAQ section to README
- Clarify qBittorrent integration requires QUI, not native Web UI

### CI/CD
- Replace GitLab template with pip-audit for dependency scanning
- Add GitLab native dependency scanning

### Chores
- Remove CLAUDE.md from repo and add to .gitignore
- Remove renovate.json and Watchtower label from docker-compose

---

## v0.4.0

### Added
- Saved filter presets in library
- Configurable page size (25 / 50 / 100 / 200 items)
- Export library to CSV with current filters applied
- Advanced rule conditions: max size, watch status filter, resolution checkboxes
- Seerr request deletion button in media detail panel

---

## v0.3.0

### Added
- Responsive layout with mobile hamburger navigation
- Library filters sidebar drawer on mobile
- PWA support (installable on iOS/Android)

---

## v0.2.0

### Added
- TMDB integration — streaming providers, poster caching
- qBittorrent integration — delete torrents on purge
- Seerr (Overseerr/Jellyseerr) integration
- ntfy and Gotify notifications
- Action history page

---

## v0.1.0

### Added
- Initial release
- Plex/Jellyfin library sync
- Sonarr/Radarr cross-reference
- Basic automation rules engine
- Dashboard with library stats
