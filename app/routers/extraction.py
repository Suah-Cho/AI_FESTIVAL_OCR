"""서류 정보 추출 전용 라우터 (엑셀 검증과 분리)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from app.core.config import TEMPLATES_DIR, get_settings
from app.services import extraction_session_service as ess
from app.services import field_extraction_service as fes
from app.services.field_extraction_service import FieldExtractionError

router = APIRouter()

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/extract", response_class=HTMLResponse, tags=["page"])
def extract_page() -> HTMLResponse:
    html = (TEMPLATES_DIR / "extract.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@router.get("/api/extract/doc-types", tags=["extract"])
def doc_types():
    """자주 쓰는 추출 항목 프리셋 (입력란 자동 채우기용)."""
    return JSONResponse({"doc_types": fes.list_doc_types()})


async def _save_zip(upload: UploadFile) -> str:
    name = upload.filename or ""
    if Path(name).suffix.lower() != ".zip":
        await upload.close()
        raise FieldExtractionError("ZIP 파일(.zip)을 업로드해 주세요.")
    tmp_dir = Path(tempfile.mkdtemp(prefix="upload_extract_"))
    dest = tmp_dir / Path(name).name
    try:
        dest.write_bytes(await upload.read())
    finally:
        await upload.close()
    return str(dest)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/api/extract/upload", tags=["extract"])
async def extract_upload(
    file: UploadFile = File(...),
    target_fields: str = Form(...),
    doc_type: str = Form(""),
):
    """ZIP 업로드 → 추출 대상 목록·미리보기 표 반환 (LLM 호출 없음)."""
    try:
        zip_path = await _save_zip(file)
        zip_name = Path(file.filename or "extract.zip").name
        prepared = fes.prepare_zip_extraction(
            zip_path,
            target_fields,
            doc_type=doc_type.strip(),
            source_zip_name=zip_name,
        )
    except FieldExtractionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"detail": f"파일 처리 중 오류: {exc}"})

    session = ess.store.create(prepared)
    return JSONResponse(fes.build_preview_response(session.id, prepared))


@router.post("/api/extract/configure/{session_id}", tags=["extract"])
async def extract_configure(
    session_id: str,
    body: dict = Body(default_factory=dict),
):
    """추출 시작 전 항목별 출력 형식을 저장한다."""
    session = ess.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. ZIP을 다시 업로드해 주세요."},
        )
    if session.started:
        return JSONResponse(
            status_code=400,
            content={"detail": "이미 추출이 시작된 세션입니다. ZIP을 다시 업로드해 주세요."},
        )

    raw_formats = body.get("field_formats") or {}
    if not isinstance(raw_formats, dict):
        return JSONResponse(
            status_code=400,
            content={"detail": "field_formats 는 객체 형태여야 합니다."},
        )

    saved = fes.set_field_formats(session.prepared, raw_formats)
    return JSONResponse({"ok": True, "field_formats": saved})


@router.get("/api/extract/stream/{session_id}", tags=["extract"])
async def extract_stream(session_id: str):
    """세션의 각 서류를 병렬 추출하고, 완료되는 대로 SSE 이벤트를 전송한다."""
    session = ess.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. ZIP을 다시 업로드해 주세요."},
        )

    prepared = session.prepared

    async def event_gen():
        loop = asyncio.get_running_loop()
        max_workers = max(1, get_settings().max_parallel_rows)
        session.started = True
        session.done = False

        total = prepared.total_units
        yield _sse({"type": "start", "total": total})

        if total == 0:
            session.done = True
            yield _sse({"type": "done", "summary": fes.extraction_summary(prepared)})
            return

        executor = ThreadPoolExecutor(max_workers=max_workers)

        async def run_unit(idx: int) -> tuple[int, fes.ExtractionItemResult]:
            result = await loop.run_in_executor(executor, fes.extract_unit, prepared, idx)
            return idx, result

        try:
            tasks = [run_unit(idx) for idx in range(total)]
            for coro in asyncio.as_completed(tasks):
                unit_index, result = await coro
                fes.apply_extraction_result(prepared, unit_index, result)
                display, titles = fes.display_fields_for_result(
                    prepared.field_names, result.fields
                )
                yield _sse(
                    {
                        "type": "row",
                        "row_index": unit_index + 2,
                        "fields": display,
                        "field_titles": titles,
                        "note": result.error or "OK",
                        "error": result.error,
                    }
                )
            session.done = True
            yield _sse(
                {
                    "type": "done",
                    "summary": fes.extraction_summary(prepared),
                }
            )
        finally:
            executor.shutdown(wait=False)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/extract/download/{session_id}", tags=["extract"])
def extract_download(session_id: str):
    """추출 결과 엑셀을 다운로드한다."""
    session = ess.store.get(session_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "세션을 찾을 수 없습니다. ZIP을 다시 업로드해 주세요."},
        )

    prepared = session.prepared
    output_path = fes.finalize_extraction(prepared)
    summary = fes.extraction_summary(prepared)

    return FileResponse(
        output_path,
        media_type=_XLSX_MEDIA,
        filename=prepared.download_filename(),
        headers={"X-Extract-Summary": quote(summary)},
    )
