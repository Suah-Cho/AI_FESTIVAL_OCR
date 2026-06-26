"""검증 관련 라우터.

UI 흐름:
  1) POST /api/upload          : 파일 업로드 → 엑셀을 표(JSON)로 반환 + 세션 생성
  2) GET  /api/verify/stream   : 행 단위 병렬 검증 결과를 SSE로 실시간 전송
  3) GET  /api/download        : 색칠된 결과 엑셀 다운로드

하위 호환:
  - POST /api/verify           : 업로드~검증~다운로드를 한 번에 처리(기존 방식)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from app.core.config import STATIC_DIR, TEMPLATES_DIR, get_settings
from app.schemas.verification import CellEditsRequest
from app.services import session_service as ss
from app.services import verification_service as vs
from app.services.verification_service import VerificationError, verify_uploads

router = APIRouter()

_ALLOWED_SUFFIXES = {".zip", ".xlsx"}
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/", response_class=HTMLResponse, tags=["page"])
def index() -> HTMLResponse:
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    """브라우저 기본 요청(/favicon.ico) 처리: static 폴더의 아이콘을 찾아 반환."""
    for name in ("favicon.ico", "favicon.png"):
        path = STATIC_DIR / name
        if path.is_file():
            return FileResponse(path)
    return JSONResponse(status_code=404, content={"detail": "favicon 없음"})


def _parse_columns(target_columns: str) -> list[str]:
    return [c.strip() for c in target_columns.split(",") if c.strip()]


async def _save_uploads(files: list[UploadFile]) -> list[str]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="upload_"))
    saved: list[str] = []
    for up in files:
        name = up.filename or ""
        if Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
            await up.close()
            continue
        dest = tmp_dir / Path(name).name
        try:
            dest.write_bytes(await up.read())
            saved.append(str(dest))
        finally:
            await up.close()
    return saved


@router.post("/api/upload", tags=["verify"])
async def upload(
    files: list[UploadFile] = File(...),
    target_columns: str = Form(...),
    folder_col_name: str = Form(""),
    file_no_col_name: str = Form(""),
):
    """파일을 받아 엑셀을 파싱하고, 화면에 표시할 표 데이터를 반환한다."""
    columns = _parse_columns(target_columns)
    if not columns:
        return JSONResponse(status_code=400, content={"detail": "검증할 열 이름을 입력해 주세요."})

    saved_paths = await _save_uploads(files)
    if not saved_paths:
        return JSONResponse(
            status_code=400, content={"detail": "ZIP 또는 XLSX 파일을 업로드해 주세요."}
        )

    try:
        prepared = vs.prepare_uploads(
            saved_paths,
            columns,
            folder_col_name=folder_col_name.strip() or None,
            file_no_col_name=file_no_col_name.strip() or None,
        )
    except VerificationError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"detail": f"파일 처리 중 오류: {exc}"})

    session = ss.store.create(prepared, columns)

    return JSONResponse(
        {
            "session_id": session.id,
            "header_row": prepared.header_row,
            "result_col": prepared.result_col,
            "target_columns": prepared.target_col_indices(),
            "n_rows": len(prepared.cells),
            "n_cols": len(prepared.cells[0]) if prepared.cells else 0,
            "cells": prepared.cells,
            "verify_rows": [p.row_index for p in prepared.rows],
        }
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/api/verify/stream/{session_id}", tags=["verify"])
async def verify_stream(session_id: str):
    """세션의 각 행을 병렬로 검증하고, 완료되는 대로 SSE 이벤트를 흘려보낸다."""
    session = ss.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. 파일을 다시 업로드해 주세요."},
        )

    prepared = session.prepared

    async def event_gen():
        loop = asyncio.get_running_loop()
        max_workers = max(1, get_settings().max_parallel_rows)

        # 재실행 대비: 누적 카운트 초기화
        for key in prepared.counts:
            prepared.counts[key] = 0
        session.started = True
        session.done = False

        yield _sse({"type": "start", "total": prepared.total_rows})

        if not prepared.rows:
            session.done = True
            yield _sse(
                {
                    "type": "done",
                    "counts": dict(prepared.counts),
                    "summary": vs.build_verify_result(prepared).summary_text(),
                }
            )
            return

        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            tasks = [
                loop.run_in_executor(executor, vs.verify_row, prepared, plan)
                for plan in prepared.rows
            ]
            for fut in asyncio.as_completed(tasks):
                result = await fut
                vs.apply_row_result(prepared, result)
                yield _sse(
                    {
                        "type": "row",
                        "row_index": result.row_index,
                        "result_text": result.result_text,
                        "statuses": result.statuses,
                        "expected": result.expected,
                        "extracted": result.extracted,
                    }
                )
            session.done = True
            yield _sse(
                {
                    "type": "done",
                    "counts": dict(prepared.counts),
                    "summary": vs.build_verify_result(prepared).summary_text(),
                }
            )
        finally:
            executor.shutdown(wait=False)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/api/session/{session_id}/cells", tags=["verify"])
def patch_cells(session_id: str, body: CellEditsRequest):
    """화면에서 수정한 셀 값을 세션 워크북에 반영한다."""
    session = ss.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. 파일을 다시 업로드해 주세요."},
        )
    if not body.edits:
        return JSONResponse({"updated": 0})

    edits = [e.model_dump() for e in body.edits]
    with session.lock:
        vs.apply_cell_edits(session.prepared, edits)
    return JSONResponse({"updated": len(edits)})


@router.get("/api/download/{session_id}", tags=["verify"])
def download(session_id: str):
    """검증 결과가 반영된 엑셀을 저장해 내려준다."""
    session = ss.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. 파일을 다시 업로드해 주세요."},
        )

    prepared = session.prepared
    output_path = vs.finalize(prepared)
    summary = vs.build_verify_result(prepared).summary_text()

    return FileResponse(
        output_path,
        media_type=_XLSX_MEDIA,
        filename="verified_result.xlsx",
        headers={"X-Verify-Summary": quote(summary)},
    )


@router.post("/api/verify", tags=["verify"])
async def verify(
    files: list[UploadFile] = File(...),
    target_columns: str = Form(...),
    folder_col_name: str = Form(""),
    file_no_col_name: str = Form(""),
):
    """기존 방식: 업로드~검증~결과 엑셀 반환을 한 번에 처리(하위 호환)."""
    columns = _parse_columns(target_columns)
    if not columns:
        return JSONResponse(status_code=400, content={"detail": "검증할 열 이름을 입력해 주세요."})

    saved_paths = await _save_uploads(files)
    if not saved_paths:
        return JSONResponse(
            status_code=400, content={"detail": "ZIP 또는 XLSX 파일을 업로드해 주세요."}
        )

    try:
        result = verify_uploads(
            saved_paths,
            columns,
            folder_col_name=folder_col_name.strip() or None,
            file_no_col_name=file_no_col_name.strip() or None,
        )
    except VerificationError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"detail": f"처리 중 오류: {exc}"})

    return FileResponse(
        result.output_path,
        media_type=_XLSX_MEDIA,
        filename="verified_result.xlsx",
        headers={"X-Verify-Summary": quote(result.summary_text())},
    )
