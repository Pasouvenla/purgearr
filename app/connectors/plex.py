import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PlexConnector:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "X-Plex-Token": self.token,
            }
        )

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.get(f"{self.url}{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Plex GET {path} failed: {e}")
            return None

    def test_connection(self) -> bool:
        data = self._get("/")
        return data is not None and "MediaContainer" in data

    @staticmethod
    def _delete_failure_reason(status: int) -> str:
        if status == 400:
            # Plex checks it can remove the files before doing anything and
            # answers a bare 400 when it cannot, logging nothing on its side.
            return (
                "Plex could not remove the files (HTTP 400). Its own process needs write "
                "access to the media: a read-only mount, or files owned by another user, "
                "both produce this"
            )
        if status in (401, 403):
            return (
                f"Plex refused the deletion (HTTP {status}). Check the token, and that "
                "'Allow media deletion' is enabled in its settings"
            )
        if status == 404:
            return "Plex no longer knows this item (HTTP 404)"
        return f"Plex deletion failed (HTTP {status})"

    def _delete(self, path: str) -> Optional[str]:
        """Send a DELETE. Returns None on success, or the reason it failed.

        The reason is user-facing: Plex reports every refusal the same way, so
        without an interpretation the caller can only say "it failed".
        """
        try:
            r = self.session.delete(f"{self.url}{path}", timeout=30)
        except Exception as e:
            logger.error(f"Plex DELETE {path} failed: {e}")
            return f"Plex is unreachable ({e})"
        if r.status_code < 400:
            return None
        logger.error(f"Plex DELETE {path} failed: HTTP {r.status_code}")
        return self._delete_failure_reason(r.status_code)

    def allows_media_deletion(self) -> Optional[bool]:
        """Whether the server will honour DELETE on a library item.

        Plex rejects deletion unless "Allow media deletion" is enabled in the
        server settings, and answers with a bare 401 that looks like a bad
        token. Reading the preference up-front lets the caller emit an
        actionable message instead. Returns None if the pref can't be read.
        """
        data = self._get("/:/prefs")
        if not data:
            return None
        for setting in data.get("MediaContainer", {}).get("Setting", []):
            if setting.get("id") == "allowMediaDeletion":
                return setting.get("value") in (True, 1, "1", "true")
        return None

    def delete_media(self, rating_key: str) -> Optional[str]:
        """Delete a library item, including its files on disk.

        Returns None on success, or the reason it failed.

        Plex deletes the media files themselves, not just the metadata entry.
        With hardlinked arr imports only the library link is removed, so a
        still-seeding torrent keeps the data (and the disk space) alive.
        """
        return self._delete(f"/library/metadata/{rating_key}")

    def forget_missing_files(self) -> list:
        """Make Plex drop the entries whose files are gone. Returns the failures.

        When the arr tool deletes a movie it takes the files with it, but Plex
        keeps its own library entry until it notices. Deleting through Plex
        would remove both, except Plex refuses (a bare HTTP 400) whenever its
        own process cannot write to the media — a read-only mount or files
        owned by another user, which is the normal case for an arr stack.

        Refreshing the section and emptying the trash needs no write access at
        all: Plex scans, finds the files missing and removes the entries
        itself. It is the way out of the ghost entries a purge leaves behind.
        """
        errors = []
        for section in self.get_libraries():
            key = section.get("key")
            if not key:
                continue
            for path, what in (
                (f"/library/sections/{key}/refresh", "refresh"),
                (f"/library/sections/{key}/emptyTrash", "empty trash"),
            ):
                try:
                    r = self.session.get(f"{self.url}{path}", timeout=30)
                    if r.status_code >= 400:
                        errors.append(
                            f"Plex could not {what} '{section.get('title', key)}' "
                            f"(HTTP {r.status_code})"
                        )
                except Exception as e:
                    errors.append(f"Plex is unreachable while asking it to {what} ({e})")
                    return errors
        return errors

    def get_libraries(self) -> list:
        data = self._get("/library/sections")
        if not data:
            return []
        sections = data.get("MediaContainer", {}).get("Directory", [])
        return [{"key": s["key"], "title": s["title"], "type": s["type"]} for s in sections]

    def _resolution_from_streams(self, streams: list) -> str:
        for s in streams:
            if s.get("streamType") == 1:
                height = s.get("height", 0)
                if height >= 2160:
                    return "4k"
                elif height >= 1080:
                    return "1080p"
                elif height >= 720:
                    return "720p"
                else:
                    return "sd"
        return "sd"

    def _video_codec(self, streams: list) -> str:
        for s in streams:
            if s.get("streamType") == 1:
                return s.get("codec", "")
        return ""

    def _audio_codec(self, streams: list) -> str:
        for s in streams:
            if s.get("streamType") == 2:
                return s.get("codec", "")
        return ""

    def _quality_from_media(self, media_list: list) -> str:
        if not media_list:
            return ""
        return media_list[0].get("videoResolution", "")

    def _resolution_from_media(self, media: dict) -> str:
        """Use Media-level videoResolution (always present at library level)."""
        raw = str(media.get("videoResolution", "")).lower()
        if raw in ("4k", "2160", "uhd"):
            return "4k"
        elif raw in ("1080", "1080p"):
            return "1080p"
        elif raw in ("720", "720p"):
            return "720p"
        elif raw:
            return "sd"
        return "sd"

    @staticmethod
    def _external_ids(item: dict) -> dict:
        """Extract tmdb/tvdb/imdb ids from a Plex item's Guid list.

        Requires `includeGuids=1` on the request. The item-level `guid`
        attribute is agent-specific (`plex://movie/<hash>`) and cannot be
        cross-referenced with Sonarr/Radarr, so only the `Guid` children are
        usable. Without these ids the arr match falls back to title-only,
        which silently fails whenever naming differs between servers.
        """
        ids = {"tmdb_id": None, "tvdb_id": None, "imdb_id": None}
        for guid in item.get("Guid") or []:
            raw = guid.get("id", "") if isinstance(guid, dict) else str(guid)
            scheme, _, value = raw.partition("://")
            if not value:
                continue
            if scheme == "tmdb":
                ids["tmdb_id"] = _to_int(value)
            elif scheme == "tvdb":
                ids["tvdb_id"] = _to_int(value)
            elif scheme == "imdb":
                ids["imdb_id"] = value
        return ids

    def get_movies(self, library_key: str) -> list:
        results = []
        offset = 0
        size = 100
        while True:
            data = self._get(
                f"/library/sections/{library_key}/all",
                params={
                    "type": 1,
                    "includeGuids": 1,
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": size,
                },
            )
            if not data:
                break
            container = data.get("MediaContainer", {})
            items = container.get("Metadata", [])
            for item in items:
                media_list = item.get("Media", [])
                media = media_list[0] if media_list else {}
                parts = media.get("Part", [])
                first_part = parts[0] if parts else {}
                file_size = first_part.get("size", 0)
                file_path = first_part.get("file", "")
                genres = [g["tag"] for g in item.get("Genre", [])]
                collection = ""
                if item.get("Collection"):
                    collection = item["Collection"][0].get("tag", "")
                view_count = item.get("viewCount", 0) or 0
                last_viewed_at = item.get("lastViewedAt")
                view_offset = item.get("viewOffset", 0) or 0
                duration = item.get("duration", 1) or 1
                ext_ids = self._external_ids(item)
                results.append(
                    {
                        "source_id": str(item["ratingKey"]),
                        "title": item.get("title", ""),
                        "year": item.get("year"),
                        "genres": genres,
                        "language": item.get("languageCode", ""),
                        "file_size_bytes": file_size,
                        "file_path": file_path,
                        "resolution": self._resolution_from_media(media),
                        "video_codec": media.get("videoCodec", ""),
                        "audio_codec": media.get("audioCodec", ""),
                        "source_quality": media.get("videoResolution", ""),
                        "duration_minutes": int(item.get("duration", 0) / 60000) if item.get("duration") else None,
                        "studio": item.get("studio", ""),
                        "collection_name": collection,
                        "added_at": item.get("addedAt"),
                        "poster_path": item.get("thumb", ""),
                        "tmdb_id": ext_ids["tmdb_id"],
                        "tvdb_id": ext_ids["tvdb_id"],
                        "imdb_id": ext_ids["imdb_id"],
                        "view_count": view_count,
                        "last_viewed_at": last_viewed_at,
                        "view_offset_pct": round((view_offset / duration) * 100, 1) if view_offset else 0,
                    }
                )
            total = container.get("totalSize", len(items))
            offset += size
            if offset >= total:
                break
        return results

    def _get_series_sizes(self, library_key: str) -> dict:
        """Aggregate episode file sizes by grandparentRatingKey (series ID).

        Plex does not expose cumulative file size at the show level (type=2).
        We fetch all episodes (type=4) in the library, iterate their Media →
        Part entries, and sum sizes by grandparentRatingKey (the show's ratingKey).

        Returns: dict mapping series ratingKey (str) -> total bytes (int).
        """
        sizes: dict = {}
        offset = 0
        batch = 500
        while True:
            data = self._get(
                f"/library/sections/{library_key}/all",
                params={
                    "type": 4,  # episode
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": batch,
                },
            )
            if not data:
                break
            container = data.get("MediaContainer", {})
            items = container.get("Metadata", [])
            for item in items:
                series_key = str(item.get("grandparentRatingKey", ""))
                if not series_key:
                    continue
                for media in item.get("Media", []):
                    for part in media.get("Part", []):
                        sizes[series_key] = sizes.get(series_key, 0) + (part.get("size") or 0)
            total = container.get("totalSize", len(items))
            offset += batch
            if offset >= total:
                break
        return sizes

    def get_series(self, library_key: str) -> list:
        # Pre-fetch episode file sizes (Plex only exposes size at episode level)
        try:
            series_sizes = self._get_series_sizes(library_key)
        except Exception as e:
            logger.warning(f"Could not fetch episode sizes for library {library_key}: {e}")
            series_sizes = {}

        results = []
        offset = 0
        size = 100
        while True:
            data = self._get(
                f"/library/sections/{library_key}/all",
                params={
                    "type": 2,
                    "includeGuids": 1,
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": size,
                },
            )
            if not data:
                break
            container = data.get("MediaContainer", {})
            items = container.get("Metadata", [])
            for item in items:
                genres = [g["tag"] for g in item.get("Genre", [])]
                leaf_count = item.get("leafCount", 0)
                child_count = item.get("childCount", 0)
                viewed_leaf_count = item.get("viewedLeafCount", 0) or 0
                last_viewed_at = item.get("lastViewedAt")
                rating_key = str(item["ratingKey"])
                file_size = series_sizes.get(rating_key) or None
                ext_ids = self._external_ids(item)
                results.append(
                    {
                        "source_id": rating_key,
                        "title": item.get("title", ""),
                        "year": item.get("year"),
                        "genres": genres,
                        "language": item.get("languageCode", ""),
                        "file_size_bytes": file_size,
                        "resolution": None,
                        "video_codec": None,
                        "audio_codec": None,
                        "source_quality": None,
                        "season_count": child_count,
                        "episode_count": leaf_count,
                        "series_status": None,
                        "network": item.get("studio", ""),
                        "added_at": item.get("addedAt"),
                        "poster_path": item.get("thumb", ""),
                        "tmdb_id": ext_ids["tmdb_id"],
                        "tvdb_id": ext_ids["tvdb_id"],
                        "imdb_id": ext_ids["imdb_id"],
                        "view_count": viewed_leaf_count,
                        "last_viewed_at": last_viewed_at,
                        "view_offset_pct": round((viewed_leaf_count / leaf_count) * 100, 1) if leaf_count else 0,
                    }
                )
            total = container.get("totalSize", len(items))
            offset += size
            if offset >= total:
                break
        return results

    def get_watch_history(self) -> dict:
        """Returns dict of source_id -> {last_watched, watch_status, progress}"""
        result = {}
        data = self._get(
            "/status/sessions/history/all",
            params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 5000},
        )
        if not data:
            return result
        items = data.get("MediaContainer", {}).get("Metadata", [])
        for item in items:
            key = str(item.get("ratingKey", ""))
            if not key:
                continue
            viewed_at = item.get("viewedAt")
            existing = result.get(key)
            if not existing or (viewed_at and viewed_at > existing.get("last_watched", 0)):
                result[key] = {
                    "last_watched": viewed_at,
                    "watch_status": "watched",
                    "progress": 100.0,
                }

        # Also check in-progress items via /library/onDeck
        deck_data = self._get("/library/onDeck", params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 500})
        if deck_data:
            deck_items = deck_data.get("MediaContainer", {}).get("Metadata", [])
            for item in deck_items:
                key = str(item.get("ratingKey", ""))
                if not key:
                    continue
                view_offset = item.get("viewOffset", 0)
                duration = item.get("duration", 1) or 1
                progress = round((view_offset / duration) * 100, 1)
                result[key] = {
                    "last_watched": item.get("lastViewedAt"),
                    "watch_status": "in_progress",
                    "progress": progress,
                }
        return result

    def build_poster_url(self, thumb_path: str) -> str:
        if not thumb_path:
            return ""
        return f"{self.url}{thumb_path}?X-Plex-Token={self.token}"
