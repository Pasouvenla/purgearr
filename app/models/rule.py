from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text
from app.database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    media_type = Column(String, nullable=False, default="both")  # movie / series / both

    condition_days_unwatched = Column(Integer, nullable=False)
    condition_min_size_gb = Column(Float, nullable=True)
    condition_max_size_gb = Column(Float, nullable=True)
    condition_genres = Column(Text, nullable=True)       # JSON list (optional)
    condition_watch_status = Column(String, nullable=True)  # never_watched / unwatched / None=any
    condition_resolution = Column(Text, nullable=True)      # JSON list e.g. ["4k","1080p"]

    condition_tags = Column(Text, nullable=True)             # JSON list of tag names

    action_unmonitor_arr = Column(Boolean, default=False)
    action_delete_from_arr = Column(Boolean, default=False)
    action_delete_torrent = Column(Boolean, default=False)
    action_delete_files = Column(Boolean, default=False)
    action_delete_from_media_server = Column(Boolean, default=False)

    schedule_interval_hours = Column(Integer, nullable=True)  # None = daily global only
    priority = Column(Integer, default=0)                     # lower = runs first

    last_run = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
