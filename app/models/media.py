from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, UniqueConstraint
from app.database import Base


class MediaItem(Base):
    __tablename__ = "media_items"

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_media_source_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, nullable=False)          # plex / jellyfin
    source_id = Column(String, nullable=False)       # ID in the media server
    media_type = Column(String, nullable=False)      # movie / series

    title = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    genres = Column(Text, nullable=True)             # JSON list
    language = Column(String, nullable=True)

    file_size_bytes = Column(Integer, nullable=True)
    file_path = Column(String, nullable=True)        # absolute path on the media server FS (Plex/Jellyfin)
    arr_path = Column(String, nullable=True)         # absolute path as seen by Sonarr/Radarr
    resolution = Column(String, nullable=True)       # sd / 720p / 1080p / 4k
    video_codec = Column(String, nullable=True)
    audio_codec = Column(String, nullable=True)
    source_quality = Column(String, nullable=True)

    sonarr_id = Column(Integer, nullable=True)
    radarr_id = Column(Integer, nullable=True)
    is_monitored = Column(Boolean, nullable=True)
    tags = Column(Text, nullable=True)               # JSON list

    last_watched = Column(DateTime, nullable=True)
    date_added = Column(DateTime, nullable=True)
    watch_status = Column(String, nullable=True)     # watched / unwatched / in_progress
    watch_progress = Column(Float, nullable=True, default=0.0)

    poster_url = Column(String, nullable=True)
    tmdb_id = Column(Integer, nullable=True)
    streaming_providers = Column(Text, nullable=True)  # JSON

    # Series-only
    season_count = Column(Integer, nullable=True)
    episode_count = Column(Integer, nullable=True)
    series_status = Column(String, nullable=True)    # continuing / ended / cancelled
    network = Column(String, nullable=True)

    # Movie-only
    duration_minutes = Column(Integer, nullable=True)
    studio = Column(String, nullable=True)
    collection_name = Column(String, nullable=True)

    last_synced = Column(DateTime, default=datetime.utcnow)
