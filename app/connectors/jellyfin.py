import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class JellyfinConnector:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Emby-Token": self.token,
                "Accept": "application/json",
            }
        )
        self._user_id: Optional[str] = None

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.get(f"{self.url}{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Jellyfin GET {path} failed: {e}")
            return None

    def _get_user_id(self) -> Optional[str]:
        """Resolve and cache the Jellyfin user ID for this session.

        /Users/Me works with user-scoped tokens but returns HTTP 400 with admin
        API keys (which are not associated with a specific user). In that case we
        fall back to /Users and pick the first administrator, or the first user
        in the list if no administrator is found.
        """
        if self._user_id:
            return self._user_id
        data = self._get("/Users/Me")
        if data and data.get("Id"):
            self._user_id = data["Id"]
            return self._user_id
        users = self._get("/Users")
        if users and isinstance(users, list):
            admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
            user = admin or (users[0] if users else None)
            if user:
                self._user_id = user["Id"]
        return self._user_id

    @staticmethod
    def _delete_failure_reason(status: int) -> str:
        if status in (401, 403):
            return (
                f"Jellyfin refused the deletion (HTTP {status}). The API user needs "
                "the 'Allow media deletion' permission, and write access to the media"
            )
        if status == 404:
            return "Jellyfin no longer knows this item (HTTP 404)"
        return f"Jellyfin deletion failed (HTTP {status})"

    def _delete(self, path: str) -> Optional[str]:
        """Send a DELETE. Returns None on success, or the reason it failed."""
        try:
            r = self.session.delete(f"{self.url}{path}", timeout=30)
        except Exception as e:
            logger.error(f"Jellyfin DELETE {path} failed: {e}")
            return f"Jellyfin is unreachable ({e})"
        if r.status_code < 400:
            return None
        logger.error(f"Jellyfin DELETE {path} failed: HTTP {r.status_code}")
        return self._delete_failure_reason(r.status_code)

    def allows_media_deletion(self) -> Optional[bool]:
        """Whether the API user is allowed to delete media.

        Jellyfin gates deletion on the user policy (EnableContentDeletion, or
        a non-empty EnableContentDeletionFromFolders) and answers 401/403
        otherwise. Checking up-front lets the caller explain what to enable.
        Returns None when the policy cannot be read.
        """
        user_id = self._get_user_id()
        if not user_id:
            return None
        data = self._get(f"/Users/{user_id}")
        if not data:
            return None
        policy = data.get("Policy") or {}
        if policy.get("EnableContentDeletion"):
            return True
        return bool(policy.get("EnableContentDeletionFromFolders"))

    def delete_item(self, item_id: str) -> Optional[str]:
        """Delete a library item, including its files on disk.

        Returns None on success, or the reason it failed.
        """
        return self._delete(f"/Items/{item_id}")

    def test_connection(self) -> bool:
        data = self._get("/System/Info")
        return data is not None and "ServerName" in data

    def get_libraries(self) -> list:
        user_id = self._get_user_id()
        if not user_id:
            return []
        data = self._get(f"/Users/{user_id}/Views")
        if not data:
            return []
        items = data.get("Items", [])
        return [{"id": it["Id"], "name": it["Name"], "type": it.get("CollectionType", "")} for it in items]

    def _resolution_from_streams(self, streams: list) -> str:
        """Derive resolution from MediaStreams by taking the maximum video height.

        Using max() rather than the first video stream guards against files that
        embed cover art or secondary video tracks (often 720p or lower) before
        the main feature track in the stream list.
        """
        max_height = max(
            (s.get("Height", 0) for s in streams if s.get("Type") == "Video"),
            default=0,
        )
        if max_height >= 2160:
            return "4k"
        elif max_height >= 1080:
            return "1080p"
        elif max_height >= 720:
            return "720p"
        elif max_height > 0:
            return "sd"
        return "sd"

    def _video_codec(self, streams: list) -> str:
        for s in streams:
            if s.get("Type") == "Video":
                return s.get("Codec", "")
        return ""

    def _audio_codec(self, streams: list) -> str:
        for s in streams:
            if s.get("Type") == "Audio":
                return s.get("Codec", "")
        return ""

    def get_movies(self) -> list:
        user_id = self._get_user_id()
        if not user_id:
            return []
        results = []
        start = 0
        limit = 100
        while True:
            data = self._get(
                f"/Users/{user_id}/Items",
                params={
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "Fields": "MediaStreams,Genres,Studios,ProviderIds,DateCreated,Overview,ProductionYear,PremiereDate,MediaSources",
                    "StartIndex": start,
                    "Limit": limit,
                },
            )
            if not data:
                break
            items = data.get("Items", [])
            for item in items:
                media_sources = item.get("MediaSources", [])
                first_source = media_sources[0] if media_sources else {}
                size = first_source.get("Size", 0)
                file_path = first_source.get("Path", "")
                # Prefer item-level MediaStreams; fall back to first MediaSource streams
                streams = item.get("MediaStreams") or first_source.get("MediaStreams", [])
                studios = [s["Name"] for s in item.get("Studios", [])]
                provider_ids = item.get("ProviderIds", {})
                tmdb_id = int(provider_ids["Tmdb"]) if provider_ids.get("Tmdb") else None
                user_data = item.get("UserData", {})
                results.append(
                    {
                        "source_id": item["Id"],
                        "title": item.get("Name", ""),
                        "year": item.get("ProductionYear"),
                        "genres": item.get("Genres", []),
                        "language": item.get("OriginalTitle", ""),
                        "file_size_bytes": size,
                        "file_path": file_path,
                        "resolution": self._resolution_from_streams(streams),
                        "video_codec": self._video_codec(streams),
                        "audio_codec": self._audio_codec(streams),
                        "source_quality": "",
                        "duration_minutes": int(item.get("RunTimeTicks", 0) / 600000000) if item.get("RunTimeTicks") else None,
                        "studio": studios[0] if studios else "",
                        "collection_name": "",
                        "added_at": item.get("DateCreated"),
                        "poster_path": f"/Items/{item['Id']}/Images/Primary",
                        "tmdb_id": tmdb_id,
                        "user_data": user_data,
                    }
                )
            total = data.get("TotalRecordCount", len(items))
            start += limit
            if start >= total:
                break
        return results

    def _get_series_sizes(self, user_id: str) -> dict:
        """Aggregate episode file sizes by SeriesId.

        Jellyfin exposes file size only at the episode (MediaSource) level —
        there is no cumulative size field on the Series object. We fetch all
        episodes in batches of 500 and sum their MediaSource sizes per SeriesId.

        Returns: dict mapping series_id (str) -> total bytes (int).
        """
        sizes: dict = {}
        start = 0
        limit = 500
        while True:
            data = self._get(
                f"/Users/{user_id}/Items",
                params={
                    "IncludeItemTypes": "Episode",
                    "Recursive": "true",
                    "Fields": "MediaSources",
                    "StartIndex": start,
                    "Limit": limit,
                },
            )
            if not data:
                break
            items = data.get("Items", [])
            for item in items:
                series_id = item.get("SeriesId", "")
                if not series_id:
                    continue
                for ms in item.get("MediaSources", []):
                    sizes[series_id] = sizes.get(series_id, 0) + (ms.get("Size") or 0)
            total = data.get("TotalRecordCount", len(items))
            start += limit
            if start >= total:
                break
        return sizes

    def get_series(self) -> list:
        user_id = self._get_user_id()
        if not user_id:
            return []

        # Pre-fetch episode file sizes (Jellyfin doesn't aggregate at series level)
        try:
            series_sizes = self._get_series_sizes(user_id)
        except Exception as e:
            logger.warning(f"Could not fetch Jellyfin episode sizes: {e}")
            series_sizes = {}

        results = []
        start = 0
        limit = 100
        while True:
            data = self._get(
                f"/Users/{user_id}/Items",
                params={
                    "IncludeItemTypes": "Series",
                    "Recursive": "true",
                    "Fields": "Genres,Studios,ProviderIds,DateCreated,ChildCount,RecursiveItemCount,ProductionYear,Path",
                    "StartIndex": start,
                    "Limit": limit,
                },
            )
            if not data:
                break
            items = data.get("Items", [])
            for item in items:
                provider_ids = item.get("ProviderIds", {})
                tmdb_id = int(provider_ids["Tmdb"]) if provider_ids.get("Tmdb") else None
                series_id = item["Id"]
                file_size = series_sizes.get(series_id) or None
                results.append(
                    {
                        "source_id": series_id,
                        "title": item.get("Name", ""),
                        "year": item.get("ProductionYear"),
                        "genres": item.get("Genres", []),
                        "language": "",
                        "file_size_bytes": file_size,
                        "file_path": item.get("Path", "") or None,
                        "resolution": None,
                        "video_codec": None,
                        "audio_codec": None,
                        "source_quality": None,
                        "season_count": item.get("ChildCount", 0),
                        "episode_count": item.get("RecursiveItemCount", 0),
                        "series_status": item.get("Status", ""),
                        "network": "",
                        "added_at": item.get("DateCreated"),
                        "poster_path": f"/Items/{series_id}/Images/Primary",
                        "tmdb_id": tmdb_id,
                        "user_data": item.get("UserData", {}),
                    }
                )
            total = data.get("TotalRecordCount", len(items))
            start += limit
            if start >= total:
                break
        return results

    def get_watch_history(self) -> dict:
        user_id = self._get_user_id()
        if not user_id:
            return {}
        result = {}
        start = 0
        limit = 500
        while True:
            data = self._get(
                f"/Users/{user_id}/Items",
                params={
                    "Recursive": "true",
                    "Filters": "IsPlayed",
                    "Fields": "UserData",
                    "StartIndex": start,
                    "Limit": limit,
                },
            )
            if not data:
                break
            items = data.get("Items", [])
            for item in items:
                user_data = item.get("UserData", {})
                last_played = user_data.get("LastPlayedDate")
                result[item["Id"]] = {
                    "last_watched": last_played,
                    "watch_status": "watched",
                    "progress": 100.0,
                }
            total = data.get("TotalRecordCount", len(items))
            start += limit
            if start >= total:
                break

        # In-progress
        data = self._get(
            f"/Users/{user_id}/Items/Resume",
            params={"Fields": "UserData"},
        )
        if data:
            for item in data.get("Items", []):
                user_data = item.get("UserData", {})
                ticks = user_data.get("PlaybackPositionTicks", 0)
                run_ticks = item.get("RunTimeTicks", 1) or 1
                progress = round((ticks / run_ticks) * 100, 1)
                result[item["Id"]] = {
                    "last_watched": user_data.get("LastPlayedDate"),
                    "watch_status": "in_progress",
                    "progress": progress,
                }
        return result

    def build_poster_url(self, item_id: str) -> str:
        return f"{self.url}/Items/{item_id}/Images/Primary?quality=90"
