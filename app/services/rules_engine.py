import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.media import MediaItem
from app.models.rule import Rule
from app.models.action_log import ActionLog
from app.models.settings import AppSettings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

from app.services import deletion


def _get_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    return row.value if row else None


def _get_notifier(db: Session):
    notifier_type = _get_setting(db, "notifier_type") or "none"
    if notifier_type == "none":
        return None
    notifier_url = _get_setting(db, "notifier_url") or ""
    notifier_token = _get_setting(db, "notifier_token") or ""
    from app.connectors.notifier import Notifier
    return Notifier(notifier_type, notifier_url, notifier_token)


def _send_rule_webhook(db: Session, rule: Rule, matched: int, deleted: int, freed_bytes: int) -> None:
    webhook_url = _get_setting(db, "webhook_url") or ""
    if not webhook_url:
        return
    webhook_secret = _get_setting(db, "webhook_secret") or ""
    from app.connectors.webhook import WebhookSender
    WebhookSender(webhook_url, webhook_secret).send(
        "rule_executed",
        {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "matched": matched,
            "deleted": deleted,
            "freed_gb": round(freed_bytes / (1024 ** 3), 2),
        },
    )


def _log_rule_action(
    db: Session,
    *,
    rule: Rule,
    media_title: str,
    media_type: Optional[str],
    success: bool,
    action: str = "rule_delete",
    error_message: Optional[str] = None,
    freed_bytes: Optional[int] = None,
):
    entry = ActionLog(
        timestamp=datetime.utcnow(),
        media_title=media_title,
        media_type=media_type,
        action=action,
        triggered_by="rule",
        rule_id=rule.id,
        rule_name=rule.name,
        success=success,
        error_message=error_message,
        freed_bytes=freed_bytes,
    )
    db.add(entry)


def evaluate_rule(rule: Rule, items: list) -> list:
    """Return the list of MediaItems that satisfy all conditions of the rule.

    Conditions are evaluated in order from cheapest (type, recency) to most
    expensive (JSON-parsed genres/tags). All conditions are ANDed — an item
    must pass every filter to be included.

    JSON fields (genres, resolution, tags) are stored as JSON arrays in the DB
    and parsed here. Invalid JSON is silently ignored (treated as "no filter").
    """
    matched = []
    # Items watched more recently than this date are always excluded
    cutoff = datetime.utcnow() - timedelta(days=rule.condition_days_unwatched)

    # Parse JSON list conditions once, outside the item loop
    condition_genres = None
    if rule.condition_genres:
        try:
            condition_genres = [g.lower() for g in json.loads(rule.condition_genres)]
        except Exception:
            pass

    condition_resolutions = None
    if getattr(rule, "condition_resolution", None):
        try:
            condition_resolutions = [r.lower() for r in json.loads(rule.condition_resolution)]
        except Exception:
            pass

    condition_tags = None
    if getattr(rule, "condition_tags", None):
        try:
            condition_tags = [t.lower() for t in json.loads(rule.condition_tags)]
        except Exception:
            pass

    for item in items:
        # 1. Media type filter
        if rule.media_type != "both":
            if item.media_type != rule.media_type:
                continue

        # 2. Recency — skip if watched within the cutoff window
        if item.last_watched and item.last_watched > cutoff:
            continue
        # Never act on items currently being watched
        if item.watch_status == "in_progress":
            continue

        # 3. Watch status condition (any / unwatched / never_watched)
        # "never_watched" is stricter than "unwatched": last_watched must also be None
        # to exclude items where Plex/Jellyfin lost the history but still recorded a date.
        watch_status_cond = getattr(rule, "condition_watch_status", None)
        if watch_status_cond == "never_watched":
            if item.watch_status != "unwatched" or item.last_watched is not None:
                continue
        elif watch_status_cond == "unwatched":
            # watch_status is the authoritative field — exclude anything that isn't "unwatched"
            # (the previous AND with last_watched was wrong: a "watched" item with no date
            # stored would incorrectly pass the filter)
            if item.watch_status != "unwatched":
                continue

        size_gb = (item.file_size_bytes or 0) / (1024 ** 3)

        if rule.condition_min_size_gb is not None:
            if size_gb < rule.condition_min_size_gb:
                continue

        max_size = getattr(rule, "condition_max_size_gb", None)
        if max_size is not None:
            if size_gb > max_size:
                continue

        if condition_resolutions:
            item_res = (item.resolution or "").lower()
            if item_res not in condition_resolutions:
                continue

        if condition_genres:
            item_genres = []
            if item.genres:
                try:
                    item_genres = [g.lower() for g in json.loads(item.genres)]
                except Exception:
                    pass
            if not any(g in item_genres for g in condition_genres):
                continue

        if condition_tags:
            item_tags = []
            if item.tags:
                try:
                    item_tags = [t.lower() for t in json.loads(item.tags)]
                except Exception:
                    pass
            if not any(t in item_tags for t in condition_tags):
                continue

        matched.append(item)

    return matched


def execute_rule(rule: Rule, db: Session, dry_run: bool = False) -> dict:
    """Run a single rule: evaluate all DB items, then execute configured actions.

    Action order matters:
      1. Unmonitor (if unmonitor-only — skips delete logic entirely)
      2. Delete from Sonarr/Radarr (with or without files)
      3. Delete torrent from qBittorrent (matched by title — imprecise, use with care)
      4. Remove item from DB, log to ActionLog

    Connectors are instantiated lazily (only if the relevant action is enabled)
    to avoid unnecessary API calls when the feature is not configured.

    When dry_run=True: evaluate matching items but do NOT perform any actions.
    Returns matched items list with metadata for the Preview UI.
    """
    items = db.query(MediaItem).all()
    matched = evaluate_rule(rule, items)

    if dry_run:
        preview_items = []
        for item in matched:
            preview_items.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "media_type": item.media_type,
                    "file_size_bytes": item.file_size_bytes,
                    "last_watched": item.last_watched.isoformat() if item.last_watched else None,
                }
            )
        return {"matched": len(matched), "items": preview_items, "dry_run": True}

    actioned = 0
    deleted = 0
    unmonitored = 0
    errors = []
    total_freed_bytes = 0

    sonarr_connector = None
    radarr_connector = None
    qbit_connector = None
    media_server = None
    media_server_name = "Plex"

    if getattr(rule, "action_delete_from_media_server", False):
        from app.routes.actions import _get_media_server
        media_server, media_server_name = _get_media_server(db)

    needs_arr = (
        getattr(rule, "action_unmonitor_arr", False)
        or rule.action_delete_from_arr
        or rule.action_delete_files
    )
    if needs_arr:
        sonarr_url = _get_setting(db, "sonarr_url")
        sonarr_key = _get_setting(db, "sonarr_api_key")
        if sonarr_url and sonarr_key:
            from app.connectors.sonarr import SonarrConnector
            sonarr_connector = SonarrConnector(sonarr_url, sonarr_key)

        radarr_url = _get_setting(db, "radarr_url")
        radarr_key = _get_setting(db, "radarr_api_key")
        if radarr_url and radarr_key:
            from app.connectors.radarr import RadarrConnector
            radarr_connector = RadarrConnector(radarr_url, radarr_key)

    if rule.action_delete_torrent:
        qbit_url = _get_setting(db, "qbit_url")
        qbit_api_key = _get_setting(db, "qbit_api_key")
        qbit_instance_id = int(_get_setting(db, "qbit_instance_id") or "1")
        if qbit_url and qbit_api_key:
            from app.connectors.qbittorrent import QBittorrentConnector
            qbit_connector = QBittorrentConnector(qbit_url, qbit_api_key, qbit_instance_id)

    connectors = deletion.Connectors(
        sonarr=sonarr_connector,
        radarr=radarr_connector,
        qbit=qbit_connector,
        media_server=media_server,
        media_server_name=media_server_name,
    )

    for item in matched:
        item_errors = []
        success = True

        # --- Unmonitor (no delete) ---
        if getattr(rule, "action_unmonitor_arr", False) and not rule.action_delete_from_arr:
            # Skip items already unmonitored (idempotency — avoids redundant API calls)
            if item.is_monitored is False:
                continue
            if item.media_type == "series" and item.sonarr_id and sonarr_connector:
                ok = sonarr_connector.unmonitor_series(item.sonarr_id)
                if ok:
                    item.is_monitored = False
                    unmonitored += 1
                else:
                    item_errors.append(f"Sonarr unmonitor failed for {item.title}")
                    success = False
            elif item.media_type == "movie" and item.radarr_id and radarr_connector:
                ok = radarr_connector.unmonitor_movie(item.radarr_id)
                if ok:
                    item.is_monitored = False
                    unmonitored += 1
                else:
                    item_errors.append(f"Radarr unmonitor failed for {item.title}")
                    success = False

            if success:
                actioned += 1
            if item_errors:
                errors.extend(item_errors)
            _log_rule_action(
                db,
                rule=rule,
                action="rule_unmonitor",
                media_title=item.title,
                media_type=item.media_type,
                success=success,
                error_message="; ".join(item_errors) if item_errors else None,
            )
            continue  # unmonitor-only: skip delete logic

        # --- Delete ---
        # One service for both routes: the manual route and this one used to
        # carry their own copy of these branches, so a fix had to be written
        # twice and the two drifted.
        outcome = deletion.delete_item(
            item,
            deletion.Options(
                from_arr=rule.action_delete_from_arr,
                files=rule.action_delete_files,
                torrent=rule.action_delete_torrent,
                from_media_server=getattr(rule, "action_delete_from_media_server", False),
            ),
            connectors,
        )

        freed, freed_certain = outcome.freed(
            item,
            deletion.Options(
                files=rule.action_delete_files,
                torrent=rule.action_delete_torrent,
                from_media_server=getattr(rule, "action_delete_from_media_server", False),
            ),
        )
        freed = freed or None

        titre = item.title
        type_media = item.media_type
        # The item leaves the library when the media is gone, even if a side
        # task failed. An unmatched item still never counts as deleted: no
        # primary target succeeded, so the row stays and the arr tool keeps it.
        if outcome.media_removed:
            # Only certain figures feed the total reported to the webhook:
            # an upper bound repeated run after run drifts a long way.
            if freed_certain:
                total_freed_bytes += freed or 0
            deleted += 1
            actioned += 1
            db.delete(item)

        if outcome.errors:
            errors.extend(outcome.errors)

        _log_rule_action(
            db,
            rule=rule,
            action="rule_delete",
            media_title=titre,
            media_type=type_media,
            success=outcome.success,
            error_message="; ".join(outcome.errors) if outcome.errors else None,
            freed_bytes=freed,
        )

    # Whatever removed the files, the media server keeps its entries until it
    # is asked to look again. Needs no write access, unlike deleting through it.
    if actioned and (rule.action_delete_files or getattr(rule, "action_delete_from_media_server", False)):
        if connectors.media_server is None:
            from app.routes.actions import _get_media_server
            connectors.media_server, connectors.media_server_name = _get_media_server(db)
        deletion.forget_missing_files(connectors.media_server)

        rule.last_run = datetime.utcnow()
    db.commit()

    if actioned > 0:
        try:
            notifier = _get_notifier(db)
            if notifier:
                if deleted > 0:
                    size_gb = total_freed_bytes / (1024 ** 3)
                    msg = f"Deleted {deleted} items, freed {size_gb:.1f} GB"
                else:
                    msg = f"Unmonitored {unmonitored} items"
                notifier.send(
                    title=f"Purgearr — Rule '{rule.name}' executed",
                    message=msg,
                )
        except Exception as e:
            logger.error(f"Notification send failed: {e}")

        try:
            _send_rule_webhook(db, rule, len(matched), deleted, total_freed_bytes)
        except Exception as e:
            logger.error(f"Rule webhook failed: {e}")

    return {"matched": len(matched), "actioned": actioned, "deleted": deleted, "unmonitored": unmonitored, "errors": errors}


def run_all_rules(notify_on_completion: bool = False) -> dict:
    """Called by scheduler — runs all enabled rules."""
    db = SessionLocal()
    total_matched = 0
    total_deleted = 0
    total_unmonitored = 0
    total_errors = []
    try:
        rules = db.query(Rule).filter(Rule.enabled == True).order_by(Rule.priority.asc()).all()  # noqa: E712
        for rule in rules:
            try:
                result = execute_rule(rule, db)
                total_matched += result["matched"]
                total_deleted += result["deleted"]
                total_unmonitored += result.get("unmonitored", 0)
                total_errors.extend(result["errors"])
            except Exception as e:
                logger.error(f"Rule {rule.name} execution error: {e}")
                total_errors.append(f"Rule {rule.name}: {e}")

        if notify_on_completion and (total_deleted > 0 or total_unmonitored > 0):
            try:
                notifier_type = _get_setting(db, "notifier_type") or "none"
                if notifier_type != "none":
                    notifier_url = _get_setting(db, "notifier_url") or ""
                    notifier_token = _get_setting(db, "notifier_token") or ""
                    from app.connectors.notifier import Notifier
                    n = Notifier(notifier_type, notifier_url, notifier_token)
                    parts = []
                    if total_deleted: parts.append(f"deleted {total_deleted}")
                    if total_unmonitored: parts.append(f"unmonitored {total_unmonitored}")
                    n.send(
                        title="Purgearr — Auto-sync rules completed",
                        message=f"Matched {total_matched}, {', '.join(parts)} items",
                    )
            except Exception as e:
                logger.error(f"Auto-sync notification failed: {e}")
    finally:
        db.close()
    return {"matched": total_matched, "deleted": total_deleted, "unmonitored": total_unmonitored, "errors": total_errors}
