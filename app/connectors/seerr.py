import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class SeerrConnector:
    """Supports both Overseerr and Jellyseerr (same API)."""

    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.get(f"{self.url}/api/v1{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Seerr GET {path} failed: {e}")
            return None

    def _delete(self, path: str) -> bool:
        try:
            r = self.session.delete(f"{self.url}/api/v1{path}", timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Seerr DELETE {path} failed: {e}")
            return False

    def test_connection(self) -> bool:
        data = self._get("/status")
        return data is not None and "version" in data

    def get_requests(self, media_type: Optional[str] = None, status: Optional[str] = None) -> list:
        """
        GET /api/v1/request?take=100&skip=0&sort=added
        Returns list of {id, type, status, media: {tmdbId, tvdbId, title}}
        Fetches all pages automatically.

        Filtering uses `mediaType` and `filter`: Overseerr rejects the request
        with HTTP 400 for any unknown query parameter, so the earlier `type`
        and `status` names made every filtered call fail and return an empty
        list — which silently looked like "no matching request".
        """
        all_results = []
        skip = 0
        take = 100

        while True:
            params: dict = {"take": take, "skip": skip, "sort": "added"}
            if media_type:
                params["mediaType"] = media_type
            if status:
                params["filter"] = status

            data = self._get("/request", params=params)
            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            for req in results:
                media = req.get("media", {})
                all_results.append(
                    {
                        "id": req.get("id"),
                        "type": req.get("type"),
                        "status": req.get("status"),
                        "media": {
                            "tmdbId": media.get("tmdbId"),
                            "tvdbId": media.get("tvdbId"),
                            "title": media.get("title") or media.get("originalTitle", ""),
                        },
                    }
                )

            page_info = data.get("pageInfo", {})
            total = page_info.get("results", len(all_results))
            if skip + take >= total:
                break
            skip += take

        return all_results

    def delete_request(self, request_id: int) -> bool:
        return self._delete(f"/request/{request_id}")

    def find_request_for_media(self, tmdb_id: int, media_type: str) -> Optional[dict]:
        """Find a Seerr request matching this tmdb_id and media_type."""
        # Map purgearr types to seerr types
        seerr_type = "movie" if media_type == "movie" else "tv"
        requests_list = self.get_requests(media_type=seerr_type)
        for req in requests_list:
            media = req.get("media", {})
            if media.get("tmdbId") == tmdb_id:
                return req
        return None
