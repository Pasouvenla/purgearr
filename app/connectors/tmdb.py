import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w92"


class TMDBConnector:
    def __init__(self, api_key: str, country: str = "FR"):
        self.api_key = api_key
        self.country = country
        self.session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        try:
            p = {"api_key": self.api_key}
            if params:
                p.update(params)
            r = self.session.get(f"{TMDB_BASE}{path}", params=p, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"TMDB GET {path} failed: {e}")
            return None

    def _extract_providers(self, data: Optional[dict], country: str) -> list:
        if not data:
            return []
        results = data.get("results", {}).get(country, {})
        if not results:
            return []
        providers = []
        for ptype in ("flatrate", "rent", "buy"):
            for p in results.get(ptype, []):
                providers.append(
                    {
                        "provider_name": p.get("provider_name", ""),
                        "logo_url": f"{TMDB_IMAGE_BASE}{p['logo_path']}" if p.get("logo_path") else "",
                        "type": ptype,
                    }
                )
        # Deduplicate by provider_name+type
        seen = set()
        unique = []
        for p in providers:
            key = (p["provider_name"], p["type"])
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def get_movie_providers(self, tmdb_id: int, country: Optional[str] = None) -> list:
        country = country or self.country
        data = self._get(f"/movie/{tmdb_id}/watch/providers")
        return self._extract_providers(data, country)

    def get_series_providers(self, tmdb_id: int, country: Optional[str] = None) -> list:
        country = country or self.country
        data = self._get(f"/tv/{tmdb_id}/watch/providers")
        return self._extract_providers(data, country)

    def find_tmdb_id(self, title: str, year: Optional[int], media_type: str) -> Optional[int]:
        """Search TMDB for a title. media_type: 'movie' or 'series'."""
        endpoint = "/search/movie" if media_type == "movie" else "/search/tv"
        params: dict = {"query": title, "language": "en-US"}
        if year:
            params["year" if media_type == "movie" else "first_air_date_year"] = year
        data = self._get(endpoint, params=params)
        if not data:
            return None
        results = data.get("results", [])
        if not results:
            return None
        return results[0].get("id")

    def get_movie_poster(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/movie/{tmdb_id}", params={"language": "en-US"})
        if data and data.get("poster_path"):
            return f"https://image.tmdb.org/t/p/w342{data['poster_path']}"
        return None

    def get_series_poster(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/tv/{tmdb_id}", params={"language": "en-US"})
        if data and data.get("poster_path"):
            return f"https://image.tmdb.org/t/p/w342{data['poster_path']}"
        return None
