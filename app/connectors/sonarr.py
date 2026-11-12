import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class SonarrConnector:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.get(f"{self.url}/api/v3{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Sonarr GET {path} failed: {e}")
            return None

    def _post(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.post(f"{self.url}/api/v3{path}", json=json, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Sonarr POST {path} failed: {e}")
            return None

    def _delete(self, path: str, params: Optional[dict] = None) -> bool:
        try:
            r = self.session.delete(f"{self.url}/api/v3{path}", params=params, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Sonarr DELETE {path} failed: {e}")
            return False

    def test_connection(self) -> bool:
        data = self._get("/system/status")
        return data is not None and "appName" in data

    def get_series(self) -> list:
        data = self._get("/series")
        if not data or not isinstance(data, list):
            return []
        result = []
        for s in data:
            stats = s.get("statistics", {})
            result.append(
                {
                    "id": s["id"],
                    "title": s.get("title", ""),
                    "tvdb_id": s.get("tvdbId"),
                    "tmdb_id": s.get("tmdbId"),
                    "imdb_id": s.get("imdbId"),
                    "monitored": s.get("monitored", False),
                    "tags": s.get("tags", []),
                    "path": s.get("path", ""),
                    "size_on_disk": stats.get("sizeOnDisk", 0),
                    "status": s.get("status", ""),
                    "year": s.get("year"),
                    "season_count": stats.get("seasonCount", 0),
                    "episode_count": stats.get("totalEpisodeCount", 0),
                    "network": s.get("network", ""),
                }
            )
        return result

    def search_series(self, sonarr_id: int) -> bool:
        result = self._post(
            "/command",
            json={"name": "SeriesSearch", "seriesId": sonarr_id},
        )
        return result is not None

    def get_download_ids(self, sonarr_id: int) -> list:
        """Infohashes of every download this item was grabbed from.

        The history keeps one entry per grab and per import, each carrying the
        downloadId the client knows the torrent by. It is the only dependable
        link between a library item and its torrents: matching on the title
        fails as soon as the release name spells it differently.
        """
        data = self._get("/history/series", {"seriesId": sonarr_id})
        if not isinstance(data, list):
            return []
        vus = []
        for ev in data:
            ident = (ev.get("downloadId") or "").strip()
            if ident and ident not in vus:
                vus.append(ident)
        return vus

    def delete_series(self, sonarr_id: int, delete_files: bool = False) -> bool:
        return self._delete(
            f"/series/{sonarr_id}",
            params={"deleteFiles": str(delete_files).lower()},
        )

    def _put(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.put(f"{self.url}/api/v3{path}", json=json, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Sonarr PUT {path} failed: {e}")
            return None

    def _delete_json(self, path: str, json: Optional[dict] = None) -> bool:
        try:
            r = self.session.delete(f"{self.url}/api/v3{path}", json=json, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Sonarr DELETE {path} failed: {e}")
            return False

    def get_tags(self) -> list:
        data = self._get("/tag")
        if not data or not isinstance(data, list):
            return []
        return [{"id": t["id"], "label": t["label"]} for t in data]

    def get_seasons(self, sonarr_id: int) -> list:
        """Return list of {season_number, episode_count, size_on_disk, monitored}."""
        data = self._get(f"/series/{sonarr_id}")
        if not data:
            return []
        seasons = data.get("seasons", [])
        result = []
        for s in seasons:
            stats = s.get("statistics", {})
            result.append(
                {
                    "season_number": s.get("seasonNumber", 0),
                    "episode_count": stats.get("totalEpisodeCount", stats.get("episodeCount", 0)),
                    "size_on_disk": stats.get("sizeOnDisk", 0),
                    "monitored": s.get("monitored", False),
                }
            )
        return result

    def delete_season_files(self, sonarr_id: int, season_number: int) -> bool:
        """Delete all episode files for a given season."""
        files = self._get("/episodefile", params={"seriesId": sonarr_id, "seasonNumber": season_number})
        if not files or not isinstance(files, list):
            return True  # nothing to delete
        file_ids = [f["id"] for f in files if "id" in f]
        if not file_ids:
            return True
        return self._delete_json("/episodefile/bulk", json={"episodeFileIds": file_ids})

    def unmonitor_season(self, sonarr_id: int, season_number: int) -> bool:
        """Set a specific season to unmonitored."""
        series_data = self._get(f"/series/{sonarr_id}")
        if not series_data:
            return False
        seasons = series_data.get("seasons", [])
        for s in seasons:
            if s.get("seasonNumber") == season_number:
                s["monitored"] = False
                break
        series_data["seasons"] = seasons
        result = self._put(f"/series/{sonarr_id}", json=series_data)
        return result is not None

    def unmonitor_series(self, sonarr_id: int) -> bool:
        """Set the entire series to unmonitored."""
        series_data = self._get(f"/series/{sonarr_id}")
        if not series_data:
            return False
        series_data["monitored"] = False
        result = self._put(f"/series/{sonarr_id}", json=series_data)
        return result is not None
