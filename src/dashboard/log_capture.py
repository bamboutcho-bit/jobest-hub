"""In-memory circular ring buffer for system operational logs."""
from __future__ import annotations

import collections
import logging
from datetime import datetime, timezone
from typing import Optional


class SystemLogEntry:
    def __init__(self, timestamp: str, level: str, logger_name: str, message: str):
        self.timestamp = timestamp
        self.level = level
        self.logger_name = logger_name
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
        }


class SystemLogRingBuffer(logging.Handler):
    """Thread-safe in-memory ring buffer that retains the most recent log events."""

    def __init__(self, capacity: int = 1500):
        super().__init__()
        self.buffer = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
            entry = SystemLogEntry(
                timestamp=ts,
                level=record.levelname,
                logger_name=record.name,
                message=msg,
            )
            self.buffer.append(entry)
        except Exception:
            pass

    def get_logs(
        self,
        level: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, str]]:
        """Retrieve recent logs, optionally filtered by level and keyword."""
        entries = list(self.buffer)

        if level and level.upper() != "ALL":
            lvl = level.upper()
            entries = [e for e in entries if e.level == lvl]

        if query:
            q = query.lower()
            entries = [e for e in entries if q in e.message.lower() or q in e.logger_name.lower()]

        # Return latest first
        return [e.to_dict() for e in entries[-limit:][::-1]]


# Global singleton ring buffer attached to the root logger
system_log_buffer = SystemLogRingBuffer(capacity=1500)
system_log_buffer.setLevel(logging.INFO)

root_logger = logging.getLogger()
if system_log_buffer not in root_logger.handlers:
    root_logger.addHandler(system_log_buffer)
