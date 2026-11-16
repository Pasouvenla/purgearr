from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime
from app.database import Base


class LibrarySnapshot(Base):
    __tablename__ = "library_snapshots"

    id = Column(Integer, primary_key=True)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
    total_items = Column(Integer, default=0)
    total_movies = Column(Integer, default=0)
    total_series = Column(Integer, default=0)
    watched_items = Column(Integer, default=0)
    unwatched_items = Column(Integer, default=0)
    total_size_gb = Column(Float, default=0.0)
    movies_size_gb = Column(Float, default=0.0)
    series_size_gb = Column(Float, default=0.0)
