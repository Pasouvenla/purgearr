import logging

import requests

logger = logging.getLogger(__name__)


class TautulliConnector:
    """Tautulli API v2 connector — enriches watch history from Plex."""

    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def _get(self, cmd: str, **params) -> dict:
        try:
            r = requests.get(
                f"{self.url}/api/v2",
                params={"apikey": self.api_key, "cmd": cmd, **params},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("response", {}).get("result") == "success":
                return data["response"]["data"]
        except Exception as e:
            logger.error(f"Tautulli API error [{cmd}]: {e}")
        return {}

    def test_connection(self) -> bool:
        return bool(self._get("get_server_info"))

    def get_watch_history(self, length: int = 5000) -> dict:
        """Return {plex_rating_key: {last_watched, watch_status, progress}}."""
        data = self._get("get_history", length=length)
        rows = (data.get("data") or []) if isinstance(data, dict) else []
        result: dict = {}
        for row in rows:
            # For movies use rating_key; for episodes use grandparent_rating_key (the show)
            media_type = row.get("media_type", "")
            if media_type == "episode":
                key = str(row.get("grandparent_rating_key") or "")
            else:
                key = str(row.get("rating_key") or "")
            if not key:
                continue
            watched_at = row.get("stopped") or row.get("started") or 0
            percent = float(row.get("percent_complete") or 0)
            existing = result.get(key)
            if not existing or watched_at > (existing.get("last_watched") or 0):
                if percent >= 80:
                    status = "watched"
                elif percent > 5:
                    status = "in_progress"
                else:
                    status = "unwatched"
                result[key] = {
                    "last_watched": watched_at,
                    "watch_status": status,
                    "progress": percent,
                }
        return result
