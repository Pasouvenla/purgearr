import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class QBittorrentConnector:
    """
    Connects to qBittorrent via the QUI API (api-key auth).
    QUI proxies all qBittorrent operations — no username/password needed.
    """

    def __init__(self, url: str, api_key: str, instance_id: int = 1):
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self.instance_id = int(instance_id) if instance_id else 1
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict | list]:
        try:
            r = self.session.get(f"{self.base_url}{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"QUI GET {path} failed: {e}")
            return None

    def _post(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self.session.post(f"{self.base_url}{path}", json=json, timeout=15)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {}
        except Exception as e:
            logger.error(f"QUI POST {path} failed: {e}")
            return None

    def test_connection(self) -> bool:
        data = self._get(f"/api/instances/{self.instance_id}/app-info")
        return data is not None

    def get_torrents(self) -> list:
        data = self._get(f"/api/instances/{self.instance_id}/torrents")
        if not data or "torrents" not in data:
            return []
        return [
            {
                "hash": t["hash"],
                "name": t["name"],
                "size": t.get("size", 0),
                "state": t.get("state", ""),
                "save_path": t.get("save_path", t.get("content_path", "")),
                # Cross-seed companions live in their own category (radarr.cross,
                # tv-sonarr.cross). The arr tools never see them, so a lookup that
                # filters by category would leave them holding the data.
                "category": t.get("category", ""),
            }
            for t in data["torrents"]
        ]

    def delete_torrents(self, hashes: list, delete_files: bool = False) -> bool:
        if not hashes:
            return True
        action = "deleteWithFiles" if delete_files else "delete"
        result = self._post(
            f"/api/instances/{self.instance_id}/torrents/bulk-action",
            json={"action": action, "hashes": hashes},
        )
        return result is not None

    def find_torrents_by_hashes(self, hashes) -> list:
        """Torrents matching an exact infohash, whatever their category.

        This is the reliable lookup: the arr tools record the infohash of every
        grab, so it ties a library item to its torrents without guessing. Hashes
        are compared case-insensitively because the arr tools report them
        uppercase and qBittorrent lowercase.
        """
        wanted = {h.lower() for h in hashes if h}
        if not wanted:
            return []
        return [t for t in self.get_torrents() if t["hash"].lower() in wanted]

    def find_torrents_for_title(self, title: str) -> list:
        """Last-resort lookup by title substring.

        Unreliable by construction: release names substitute separators, so
        'X-Men' does not appear in 'X.Men.2000.MULTi.2160p'. It also matches
        unrelated torrents when the title is a common word. Kept only for items
        whose infohash the arr tools no longer know, and the caller must report
        that it fell back to this.
        """
        title_lower = title.lower()
        return [t for t in self.get_torrents() if title_lower in t["name"].lower()]
