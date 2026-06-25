"""정보 추출 업로드 → 실시간 추출 → 다운로드 세션."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.services.field_extraction_service import PreparedExtraction


@dataclass
class ExtractSession:
    id: str
    prepared: PreparedExtraction
    started: bool = False
    done: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class _ExtractSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ExtractSession] = {}
        self._lock = threading.Lock()

    def create(self, prepared: PreparedExtraction) -> ExtractSession:
        sid = uuid.uuid4().hex
        session = ExtractSession(id=sid, prepared=prepared)
        with self._lock:
            self._sessions[sid] = session
        return session

    def get(self, sid: str) -> Optional[ExtractSession]:
        with self._lock:
            return self._sessions.get(sid)

    def remove(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)


store = _ExtractSessionStore()
