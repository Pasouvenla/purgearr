import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from app.database import Base


class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    media_title = Column(String, nullable=False)
    media_type = Column(String, nullable=True)       # movie | series

    action = Column(String, nullable=False)           # delete | search | unmonitor | delete_season | rule_delete
    triggered_by = Column(String, nullable=False)     # manual | rule

    rule_id = Column(Integer, nullable=True)
    rule_name = Column(String, nullable=True)

    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    freed_bytes = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)             # JSON — extra context (options chosen, etc.)

    @property
    def freed_is_estimate(self) -> bool:
        """Whether the freed figure is an upper bound rather than a measurement.

        The arr tools import by hardlink, so the library file and the torrent
        are usually one inode: removing the library side frees nothing while a
        torrent keeps seeding it. The deletion service records whether it could
        tell; anything written before it did is treated as an estimate.
        """
        if not self.freed_bytes:
            return False
        try:
            return bool(json.loads(self.details or "{}").get("freed_is_estimate", True))
        except (ValueError, TypeError):
            return True
