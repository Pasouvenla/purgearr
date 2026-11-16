from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


SETTINGS_KEYS = [
    "plex_url",
    "plex_token",
    "jellyfin_url",
    "jellyfin_token",
    "sonarr_url",
    "sonarr_api_key",
    "radarr_url",
    "radarr_api_key",
    "qbit_url",
    "qbit_api_key",
    "qbit_instance_id",
    "tmdb_api_key",
    "tmdb_country",
    "active_media_server",
    # Seerr (Overseerr / Jellyseerr)
    "seerr_url",
    "seerr_api_key",
    # Notifications
    "notifier_type",
    "notifier_url",
    "notifier_token",
    # Outgoing webhooks
    "webhook_url",
    "webhook_secret",
    # Tautulli (Plex watch history enrichment)
    "tautulli_url",
    "tautulli_api_key",
    # Auto-sync
    "sync_interval_hours",
    # Library saved filters
    "saved_filters",
]

SETTINGS_DEFAULTS = {
    "tmdb_country": "FR",
    "active_media_server": "plex",
    "notifier_type": "none",
    "sync_interval_hours": "0",
}
