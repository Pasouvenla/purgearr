import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

import requests as _requests
from sqlalchemy.orm import Session

from app.models.media import MediaItem
from app.models.settings import AppSettings

logger = logging.getLogger(__name__)

POSTER_CACHE_DIR = os.environ.get("POSTER_CACHE_DIR", "/data/poster_cache")


def _cache_poster(source: str, source_id: str, url: str) -> str:
    """Download poster to cache dir. Returns local path on success, original URL on failure."""
    if not url or not url.startswith("http"):
        return url
    try:
        os.makedirs(POSTER_CACHE_DIR, exist_ok=True)
        filename = f"{source}_{source_id}.jpg"
        filepath = os.path.join(POSTER_CACHE_DIR, filename)
        if os.path.exists(filepath):
            return f"/posters/{filename}"
        r = _requests.get(url, timeout=10, stream=True)
        r.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return f"/posters/{filename}"
    except Exception as e:
        logger.debug(f"Poster cache failed for {source}:{source_id}: {e}")
        return url


def _get_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    return row.value if row else None


def _get_connector_plex(db: Session):
    url = _get_setting(db, "plex_url")
    token = _get_setting(db, "plex_token")
    if url and token:
        from app.connectors.plex import PlexConnector
        return PlexConnector(url, token)
    return None


def _get_connector_jellyfin(db: Session):
    url = _get_setting(db, "jellyfin_url")
    token = _get_setting(db, "jellyfin_token")
    if url and token:
        from app.connectors.jellyfin import JellyfinConnector
        return JellyfinConnector(url, token)
    return None


def _get_connector_sonarr(db: Session):
    url = _get_setting(db, "sonarr_url")
    key = _get_setting(db, "sonarr_api_key")
    if url and key:
        from app.connectors.sonarr import SonarrConnector
        return SonarrConnector(url, key)
    return None


def _get_connector_radarr(db: Session):
    url = _get_setting(db, "radarr_url")
    key = _get_setting(db, "radarr_api_key")
    if url and key:
        from app.connectors.radarr import RadarrConnector
        return RadarrConnector(url, key)
    return None


def _normalize_title(title: str) -> str:
    """Loose title key used as the last-resort arr match.

    Punctuation is flattened to whitespace because media servers and arr tools
    routinely disagree on it: "Fate/stay night [Unlimited Blade Works]" and
    "Fate/Stay Night: Unlimited Blade Works" are the same show. Bracketed text
    is deliberately kept rather than stripped, since it usually carries the
    subtitle that distinguishes entries in a franchise. This is a fallback
    only — external ids are matched first, see _index_arr_items().
    """
    cleaned = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return " ".join(cleaned.split())


def _index_arr_items(items: list) -> dict:
    """Build the lookup indices used to cross-reference an arr tool.

    Matching by title alone silently fails whenever the media server and the
    arr tool name a title differently, and a missed match means the item can
    never be deleted from the arr tool. External ids are stable across both
    sides, so they are tried first and the title is only a fallback.
    """
    idx = {"tvdb": {}, "tmdb": {}, "imdb": {}, "title": {}}
    for item in items:
        if item.get("tvdb_id"):
            idx["tvdb"][int(item["tvdb_id"])] = item
        if item.get("tmdb_id"):
            idx["tmdb"][int(item["tmdb_id"])] = item
        if item.get("imdb_id"):
            idx["imdb"][str(item["imdb_id"])] = item
        idx["title"].setdefault(_normalize_title(item.get("title", "")), item)
    return idx


def _match_arr_item(idx: dict, item_data: dict, title: str) -> tuple:
    """Resolve a media-server item to an arr item.

    Returns (arr_item, how) where `how` names the key that matched, so the
    caller can log why a match succeeded or report that none did.
    """
    for key, field, cast in (
        ("tvdb", "tvdb_id", int),
        ("tmdb", "tmdb_id", int),
        ("imdb", "imdb_id", str),
    ):
        value = item_data.get(field)
        if value:
            try:
                arr = idx[key].get(cast(value))
            except (TypeError, ValueError):
                continue
            if arr:
                return arr, key
    arr = idx["title"].get(_normalize_title(title))
    return (arr, "title") if arr else (None, None)


def sync_library(db: Session) -> dict:
    """Full library sync: fetch items from the active media server and upsert into DB.

    Sync flow:
      1. Pre-fetch Sonarr/Radarr series/movies and tags (for cross-reference)
      2. (Plex only) Pre-fetch Tautulli watch history if configured
      3. Fetch items from Plex/Jellyfin, call _upsert_item() for each
      4. Remove DB items no longer present in the media server
      5. Record a LibrarySnapshot for analytics
      6. Send sync webhook if configured

    Sonarr/Radarr cross-reference is done by normalized title match (lowercase,
    stripped). This is intentionally fuzzy — arr titles may differ slightly from
    media server titles. TMDB ID is used as a secondary deduplication key.
    """
    added = 0
    updated = 0
    removed = 0
    errors = []

    active = _get_setting(db, "active_media_server") or "plex"

    # Pre-fetch Sonarr/Radarr data once — used to enrich every item during upsert
    sonarr = _get_connector_sonarr(db)
    radarr = _get_connector_radarr(db)

    # Lookup indices: external ids first, normalized title as a fallback
    sonarr_idx: dict = _index_arr_items([])
    radarr_idx: dict = _index_arr_items([])
    sonarr_tag_map: dict = {}
    radarr_tag_map: dict = {}

    if sonarr:
        try:
            sonarr_idx = _index_arr_items(sonarr.get_series())
        except Exception as e:
            logger.error(f"Sonarr series fetch failed: {e}")
            errors.append(f"Sonarr: {e}")
        try:
            for t in sonarr.get_tags():
                sonarr_tag_map[t["id"]] = t["label"]
        except Exception as e:
            logger.debug(f"Sonarr tags fetch failed: {e}")

    if radarr:
        try:
            radarr_idx = _index_arr_items(radarr.get_movies())
        except Exception as e:
            logger.error(f"Radarr movies fetch failed: {e}")
            errors.append(f"Radarr: {e}")
        try:
            for t in radarr.get_tags():
                radarr_tag_map[t["id"]] = t["label"]
        except Exception as e:
            logger.debug(f"Radarr tags fetch failed: {e}")

    # Track which source_ids we saw this sync
    seen_source_ids: set = set()

    def _upsert_item(source: str, media_type: str, item_data: dict, watch_data: dict) -> None:
        """Insert or update a single MediaItem in the DB.

        Lookup strategy:
          1. Exact match on (source, source_id) — fast path, covers most cases
          2. Fallback to (source, media_type, tmdb_id) — handles Plex setups with
             multiple library sections (e.g. "Films", "Anime", "4K") that contain
             the same title, which would otherwise create duplicate rows

        Watch status resolution:
          - item-level viewCount/lastViewedAt (from media server metadata) is preferred
          - history API data fills in when item-level data is absent
          - Tautulli data overrides Plex history when more recent (Plex only)
          - Unix timestamps (Plex) and ISO strings (Jellyfin) are both handled
        """
        nonlocal added, updated
        source_id = item_data["source_id"]
        seen_source_ids.add(f"{source}:{source_id}")

        existing = (
            db.query(MediaItem)
            .filter(MediaItem.source == source, MediaItem.source_id == source_id)
            .first()
        )

        # Fallback: deduplicate by TMDB ID — prevents multiple Plex library sections
        # (e.g. "Anime", "Séries", "Anime VOSTFR") from creating separate rows for the same item.
        if not existing:
            incoming_tmdb = item_data.get("tmdb_id")
            if incoming_tmdb:
                existing = (
                    db.query(MediaItem)
                    .filter(
                        MediaItem.source == source,
                        MediaItem.media_type == media_type,
                        MediaItem.tmdb_id == int(incoming_tmdb),
                    )
                    .first()
                )
                if existing:
                    # Update source_id to the canonical one from this sync
                    existing.source_id = source_id
                    seen_source_ids.discard(f"{source}:{existing.source_id}")
                    seen_source_ids.add(f"{source}:{source_id}")

        title = item_data.get("title", "")
        year = item_data.get("year")

        # Cross-reference arr tools
        sonarr_id = None
        radarr_id = None
        is_monitored = None
        arr_tags = []
        arr_size = None
        arr_tmdb_id = None

        arr_path = None

        if media_type == "series":
            arr, how = _match_arr_item(sonarr_idx, item_data, title)
            if arr:
                logger.debug(f"Sonarr match via {how} for '{title}'")
            elif sonarr_idx["title"]:
                logger.info(f"No Sonarr match for '{title}' — arr actions will be unavailable")
            if arr:
                sonarr_id = arr["id"]
                is_monitored = arr["monitored"]
                arr_tags = [sonarr_tag_map.get(tid, str(tid)) for tid in arr.get("tags", [])]
                arr_size = arr.get("size_on_disk")
                arr_tmdb_id = arr.get("tmdb_id")
                arr_path = arr.get("path") or None
        else:
            arr, how = _match_arr_item(radarr_idx, item_data, title)
            if arr:
                logger.debug(f"Radarr match via {how} for '{title}'")
            elif radarr_idx["title"]:
                logger.info(f"No Radarr match for '{title}' — arr actions will be unavailable")
            if arr:
                radarr_id = arr["id"]
                is_monitored = arr["monitored"]
                arr_tags = [radarr_tag_map.get(tid, str(tid)) for tid in arr.get("tags", [])]
                arr_size = arr.get("size_on_disk")
                arr_tmdb_id = arr.get("tmdb_id")
                arr_path = arr.get("path") or None

        # Watch status — prefer item-level fields (viewCount/lastViewedAt) over history API
        item_view_count = item_data.get("view_count", 0) or 0
        item_last_viewed = item_data.get("last_viewed_at")
        item_offset_pct = item_data.get("view_offset_pct", 0) or 0

        wd = watch_data.get(source_id, {})

        # Determine best last_watched timestamp (item-level takes priority if more recent)
        hist_ts = wd.get("last_watched")
        if item_last_viewed and (not hist_ts or item_last_viewed > hist_ts):
            last_watched_ts = item_last_viewed
        else:
            last_watched_ts = hist_ts

        watch_progress = wd.get("progress", item_offset_pct)

        # Convert unix timestamps (Plex) or ISO strings (Jellyfin) to datetime
        last_watched_dt = None
        if last_watched_ts:
            try:
                if isinstance(last_watched_ts, (int, float)):
                    last_watched_dt = datetime.utcfromtimestamp(last_watched_ts)
                else:
                    last_watched_dt = datetime.fromisoformat(str(last_watched_ts).rstrip("Z"))
            except Exception:
                pass

        date_added = None
        added_at = item_data.get("added_at")
        if added_at:
            try:
                if isinstance(added_at, (int, float)):
                    date_added = datetime.utcfromtimestamp(added_at)
                else:
                    date_added = datetime.fromisoformat(str(added_at).rstrip("Z"))
            except Exception:
                pass

        file_size = item_data.get("file_size_bytes") or (arr_size if arr_size else None)

        # Determine watch_status: item-level viewCount is most reliable
        if wd.get("watch_status") == "in_progress":
            watch_status = "in_progress"
        elif item_view_count > 0 or last_watched_dt:
            watch_status = "watched"
        else:
            watch_status = "unwatched"

        raw_poster_url = item_data.get("poster_url", "")
        cached_poster_url = _cache_poster(source, source_id, raw_poster_url)

        fields = {
            "source": source,
            "source_id": source_id,
            "media_type": media_type,
            "title": title,
            "year": year,
            "genres": json.dumps(item_data.get("genres", [])),
            "language": item_data.get("language", ""),
            "file_size_bytes": file_size,
            "file_path": item_data.get("file_path") or None,
            "arr_path": arr_path,
            "resolution": item_data.get("resolution"),
            "video_codec": item_data.get("video_codec"),
            "audio_codec": item_data.get("audio_codec"),
            "source_quality": item_data.get("source_quality"),
            "sonarr_id": sonarr_id,
            "radarr_id": radarr_id,
            "is_monitored": is_monitored,
            "tags": json.dumps(arr_tags),
            "last_watched": last_watched_dt,
            "date_added": date_added,
            "watch_status": watch_status,
            "watch_progress": watch_progress,
            "poster_url": cached_poster_url,
            "tmdb_id": item_data.get("tmdb_id") or arr_tmdb_id,
            "last_synced": datetime.utcnow(),
        }

        if media_type == "series":
            fields.update(
                {
                    "season_count": item_data.get("season_count"),
                    "episode_count": item_data.get("episode_count"),
                    "series_status": item_data.get("series_status"),
                    "network": item_data.get("network", ""),
                }
            )
        else:
            fields.update(
                {
                    "duration_minutes": item_data.get("duration_minutes"),
                    "studio": item_data.get("studio", ""),
                    "collection_name": item_data.get("collection_name", ""),
                }
            )

        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            updated += 1
        else:
            db.add(MediaItem(**fields))
            added += 1

    # Tautulli watch history enrichment (Plex only)
    tautulli_history: dict = {}
    if active in ("plex", "both"):
        tautulli_url = _get_setting(db, "tautulli_url")
        tautulli_key = _get_setting(db, "tautulli_api_key")
        if tautulli_url and tautulli_key:
            try:
                from app.connectors.tautulli import TautulliConnector
                tc = TautulliConnector(tautulli_url, tautulli_key)
                tautulli_history = tc.get_watch_history()
            except Exception as e:
                logger.error(f"Tautulli history fetch failed: {e}")
                errors.append(f"Tautulli: {e}")

    # --- Plex ---
    if active in ("plex", "both"):
        plex = _get_connector_plex(db)
        if plex:
            try:
                watch_history = plex.get_watch_history()
                # Merge Tautulli data — it takes precedence when available
                for key, tdata in tautulli_history.items():
                    if key not in watch_history or (
                        tdata.get("last_watched") and
                        tdata["last_watched"] > (watch_history[key].get("last_watched") or 0)
                    ):
                        watch_history[key] = tdata
            except Exception as e:
                logger.error(f"Plex watch history failed: {e}")
                errors.append(f"Plex watch history: {e}")
                watch_history = tautulli_history.copy()

            libraries = plex.get_libraries()
            for lib in libraries:
                try:
                    if lib["type"] == "movie":
                        items = plex.get_movies(lib["key"])
                        for item in items:
                            # Build full poster URL
                            item["poster_url"] = plex.build_poster_url(item.get("poster_path", ""))
                            _upsert_item("plex", "movie", item, watch_history)
                    elif lib["type"] == "show":
                        items = plex.get_series(lib["key"])
                        for item in items:
                            item["poster_url"] = plex.build_poster_url(item.get("poster_path", ""))
                            _upsert_item("plex", "series", item, watch_history)
                except Exception as e:
                    logger.error(f"Plex library sync failed for {lib}: {e}")
                    errors.append(f"Plex lib {lib.get('title')}: {e}")

    # --- Jellyfin ---
    if active in ("jellyfin", "both"):
        jf = _get_connector_jellyfin(db)
        if jf:
            try:
                watch_history = jf.get_watch_history()
            except Exception as e:
                logger.error(f"Jellyfin watch history failed: {e}")
                errors.append(f"Jellyfin watch history: {e}")
                watch_history = {}

            try:
                for item in jf.get_movies():
                    item["poster_url"] = jf.build_poster_url(item["source_id"])
                    _upsert_item("jellyfin", "movie", item, watch_history)
            except Exception as e:
                logger.error(f"Jellyfin movies sync failed: {e}")
                errors.append(f"Jellyfin movies: {e}")

            try:
                for item in jf.get_series():
                    item["poster_url"] = jf.build_poster_url(item["source_id"])
                    _upsert_item("jellyfin", "series", item, watch_history)
            except Exception as e:
                logger.error(f"Jellyfin series sync failed: {e}")
                errors.append(f"Jellyfin series: {e}")

    db.commit()

    # Mark items no longer present as removed (delete from DB)
    if seen_source_ids:
        all_items = db.query(MediaItem).all()
        for item in all_items:
            key = f"{item.source}:{item.source_id}"
            if key not in seen_source_ids and item.source in (active if active != "both" else ["plex", "jellyfin"]):
                db.delete(item)
                removed += 1
        db.commit()

    # Record library snapshot
    try:
        _record_snapshot(db)
    except Exception as e:
        logger.error(f"Snapshot record failed: {e}")

    # Send webhook
    try:
        _send_sync_webhook(db, added=added, updated=updated, removed=removed)
    except Exception as e:
        logger.error(f"Sync webhook failed: {e}")

    return {"added": added, "updated": updated, "removed": removed, "errors": errors}


def _record_snapshot(db: Session) -> None:
    from app.models.library_snapshot import LibrarySnapshot
    all_items = db.query(MediaItem).all()
    total_movies = sum(1 for i in all_items if i.media_type == "movie")
    total_series = sum(1 for i in all_items if i.media_type == "series")
    watched = sum(1 for i in all_items if i.watch_status == "watched")
    unwatched = sum(1 for i in all_items if i.watch_status == "unwatched")
    total_size = sum((i.file_size_bytes or 0) for i in all_items) / (1024 ** 3)
    movies_size = sum((i.file_size_bytes or 0) for i in all_items if i.media_type == "movie") / (1024 ** 3)
    series_size = sum((i.file_size_bytes or 0) for i in all_items if i.media_type == "series") / (1024 ** 3)
    snap = LibrarySnapshot(
        total_items=len(all_items),
        total_movies=total_movies,
        total_series=total_series,
        watched_items=watched,
        unwatched_items=unwatched,
        total_size_gb=round(total_size, 2),
        movies_size_gb=round(movies_size, 2),
        series_size_gb=round(series_size, 2),
    )
    db.add(snap)
    db.commit()


def _send_sync_webhook(db: Session, added: int, updated: int, removed: int) -> None:
    webhook_url = _get_setting(db, "webhook_url") or ""
    if not webhook_url:
        return
    webhook_secret = _get_setting(db, "webhook_secret") or ""
    from app.connectors.webhook import WebhookSender
    sender = WebhookSender(webhook_url, webhook_secret)
    sender.send("sync_completed", {"added": added, "updated": updated, "removed": removed})
