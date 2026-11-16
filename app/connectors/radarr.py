import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class RadarrConnector:
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
            logger.error(f"Radarr GET {path} failed: {e}")
            return None

    def _post(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.post(f"{self.url}/api/v3{path}", json=json, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Radarr POST {path} failed: {e}")
            return None

    def _delete(self, path: str, params: Optional[dict] = None) -> bool:
        try:
            r = self.session.delete(f"{self.url}/api/v3{path}", params=params, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Radarr DELETE {path} failed: {e}")
            return False

    def test_connection(self) -> bool:
        data = self._get("/system/status")
        return data is not None and "appName" in data

    def get_movies(self) -> list:
        data = self._get("/movie")
        if not data or not isinstance(data, list):
            return []
        result = []
        for m in data:
            result.append(
                {
                    "id": m["id"],
                    "title": m.get("title", ""),
                    "tmdb_id": m.get("tmdbId"),
                    "imdb_id": m.get("imdbId"),
                    "monitored": m.get("monitored", False),
                    "tags": m.get("tags", []),
                    "path": m.get("path", ""),
                    "size_on_disk": m.get("sizeOnDisk", 0),
                    "has_file": m.get("hasFile", False),
                    "year": m.get("year"),
                }
            )
        return result

    def search_movie(self, radarr_id: int) -> bool:
        result = self._post(
            "/command",
            json={"name": "MoviesSearch", "movieIds": [radarr_id]},
        )
        return result is not None

    def get_download_ids(self, radarr_id: int) -> list:
        """Infohashes of every download this item was grabbed from.

        The history keeps one entry per grab and per import, each carrying the
        downloadId the client knows the torrent by. It is the only dependable
        link between a library item and its torrents: matching on the title
        fails as soon as the release name spells it differently.
        """
        data = self._get("/history/movie", {"movieId": radarr_id})
        if not isinstance(data, list):
            return []
        vus = []
        for ev in data:
            ident = (ev.get("downloadId") or "").strip()
            if ident and ident not in vus:
                vus.append(ident)
        return vus

    def delete_movie(self, radarr_id: int, delete_files: bool = False) -> bool:
        return self._delete(
            f"/movie/{radarr_id}",
            params={"deleteFiles": str(delete_files).lower()},
        )

    def _put(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.put(f"{self.url}/api/v3{path}", json=json, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Radarr PUT {path} failed: {e}")
            return None

    def get_tags(self) -> list:
        data = self._get("/tag")
        if not data or not isinstance(data, list):
            return []
        return [{"id": t["id"], "label": t["label"]} for t in data]

    def unmonitor_movie(self, radarr_id: int) -> bool:
        """Set the movie to unmonitored."""
        movie_data = self._get(f"/movie/{radarr_id}")
        if not movie_data:
            return False
        movie_data["monitored"] = False
        result = self._put(f"/movie/{radarr_id}", json=movie_data)
        return result is not None
