"""업로드~검증~다운로드를 잇는 세션 상태를 메모리에 보관한다.

UI 흐름이 3단계(업로드 → 실시간 검증 → 다운로드)로 나뉘면서, 그 사이에
준비된 워크북(PreparedSheet)과 작업 폴더를 들고 있어야 한다. 데모/해커톤
규모를 가정한 단순 인메모리 저장소다 (프로세스 재시작 시 사라짐).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.services.verification_service import PreparedSheet


@dataclass
class Session:
    id: str
    prepared: PreparedSheet
    target_columns: list[str]
    started: bool = False  # 검증 스트리밍이 시작되었는지
    done: bool = False  # 모든 행 검증 완료
    lock: threading.Lock = field(default_factory=threading.Lock)


class _SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, prepared: PreparedSheet, target_columns: list[str]) -> Session:
        sid = uuid.uuid4().hex
        session = Session(id=sid, prepared=prepared, target_columns=target_columns)
        with self._lock:
            self._sessions[sid] = session
        return session

    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(sid)

    def remove(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)


store = _SessionStore()
