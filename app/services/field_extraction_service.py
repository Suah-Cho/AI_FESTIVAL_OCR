"""단일·일괄 서류 정보 추출 (검증/엑셀 대조 없음)."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.schemas.extraction import get_doc_type_preset
from app.services.document_routing import build_mixed_doc_hint, has_mixed_field_types
from app.services.extraction_service import extract_fields_from_documents
from app.services.field_aliases import format_extracted_field, is_rrn_field

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_PDF_EXTS = {".pdf"}
_ALLOWED_SUFFIXES = _IMAGE_EXTS | _PDF_EXTS

BATCH_SINGLE = "single"
BATCH_PER_FILE = "per_file"
BATCH_PER_FOLDER = "per_folder"
BATCH_MODES = {BATCH_SINGLE, BATCH_PER_FILE, BATCH_PER_FOLDER}


class FieldExtractionError(Exception):
    """추출 진행이 불가능한 사용자 입력/데이터 오류."""


@dataclass
class DocumentUnit:
    """추출 대상 서류 묶음 하나 (폴더 + 표시 이름)."""

    label: str
    folder_path: str


@dataclass
class ExtractionItemResult:
    label: str
    fields: dict[str, str]
    error: str | None = None


@dataclass
class PreparedExtraction:
    """ZIP 업로드 후 추출 준비 상태 (화면 표 + 병렬 추출)."""

    field_names: list[str]
    doc_hint: str
    units: list[DocumentUnit]
    cells: list[list[str]]
    items: list[ExtractionItemResult]
    field_formats: dict[str, str] | None = None
    source_zip_name: str = ""
    output_path: str | None = None

    def __post_init__(self) -> None:
        if self.field_formats is None:
            self.field_formats = {}

    @property
    def total_units(self) -> int:
        return len(self.units)

    def field_col_indices(self) -> dict[str, int]:
        return {name: i + 2 for i, name in enumerate(self.field_names)}

    def download_filename(self) -> str:
        stem = Path(self.source_zip_name).stem if self.source_zip_name else "extract_result"
        return f"{stem}.xlsx"


def prepare_zip_extraction(
    zip_path: str,
    target_fields: str,
    *,
    doc_type: str = "",
    source_zip_name: str = "",
) -> PreparedExtraction:
    """ZIP을 풀고 추출 대상 목록·미리보기 표를 만든다. (LLM 호출 없음)"""
    field_names = parse_target_fields(target_fields)
    doc_hint = resolve_doc_hint(doc_type or None, field_names)
    root = prepare_zip_root(zip_path)
    units = group_document_units(root, BATCH_PER_FOLDER)
    if not units:
        raise FieldExtractionError("추출할 서류를 찾지 못했습니다.")

    header = ["서류경로", *field_names]
    cells: list[list[str]] = [header]
    items: list[ExtractionItemResult] = []
    for unit in units:
        cells.append([unit.label, *[""] * len(field_names)])
        items.append(
            ExtractionItemResult(
                label=unit.label,
                fields={name: "" for name in field_names},
            )
        )

    return PreparedExtraction(
        field_names=field_names,
        doc_hint=doc_hint,
        units=units,
        cells=cells,
        items=items,
        source_zip_name=source_zip_name or Path(zip_path).name,
    )


def suggest_field_format(field_name: str) -> str:
    """항목 이름으로 자주 쓰는 출력 형식 예시를 추천한다."""
    name = field_name.strip()
    if "전화" in name:
        return "000-0000-0000"
    if any(k in name for k in ("생년월일", "연월일", "개업일", "계약일", "일자", "날짜")):
        return "YYYY-MM-DD"
    if "주민" in name or is_rrn_field(name):
        return "000000-0000000"
    if "사업자" in name and "번호" in name:
        return "000-00-00000"
    return ""


def build_field_format_hint(
    field_names: list[str], field_formats: dict[str, str]
) -> str:
    """항목별 출력 형식 지정을 LLM 프롬프트용 문장으로 만든다."""
    lines: list[str] = []
    for name in field_names:
        fmt = (field_formats.get(name) or "").strip()
        if not fmt:
            continue
        lines.append(
            f"- '{name}': 추출한 값을 반드시 다음 출력 형식으로 변환해 적으십시오: {fmt} "
            "(0·Y·M·D 등은 각각 한 자리 숫자/문자를 의미합니다. "
            "서류에 값이 없으면 빈 문자열.)"
        )
    if not lines:
        return ""
    return "항목별 출력 형식:\n" + "\n".join(lines)


def combined_doc_hint(
    doc_hint: str,
    field_names: list[str],
    field_formats: dict[str, str],
) -> str:
    """서류 종류 안내와 항목별 형식 안내를 합친다."""
    parts = [
        doc_hint.strip(),
        build_field_format_hint(field_names, field_formats).strip(),
    ]
    return "\n".join(p for p in parts if p)


def set_field_formats(prepared: PreparedExtraction, field_formats: dict[str, str]) -> dict[str, str]:
    """추출 시작 전 사용자가 지정한 항목별 출력 형식을 저장한다."""
    cleaned: dict[str, str] = {}
    for name in prepared.field_names:
        fmt = (field_formats.get(name) or "").strip()
        if fmt:
            cleaned[name] = fmt
    prepared.field_formats = cleaned
    return cleaned


def extract_unit(prepared: PreparedExtraction, unit_index: int) -> ExtractionItemResult:
    """한 서류 단위를 추출한다."""
    unit = prepared.units[unit_index]
    hint = combined_doc_hint(
        prepared.doc_hint, prepared.field_names, prepared.field_formats or {}
    )
    return _extract_one_unit_fields(unit, prepared.field_names, hint)


def display_fields_for_result(
    field_names: list[str], fields: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """화면/SSE용 표시값과 툴팁(원본) dict."""
    display: dict[str, str] = {}
    titles: dict[str, str] = {}
    for name in field_names:
        raw = fields.get(name, "") or ""
        disp, title = format_extracted_field(name, raw)
        display[name] = disp
        if title and title != disp:
            titles[name] = title
    return display, titles


def apply_extraction_result(
    prepared: PreparedExtraction,
    unit_index: int,
    result: ExtractionItemResult,
) -> None:
    """추출 결과를 메모리 표·항목 목록에 반영한다."""
    prepared.items[unit_index] = result
    row = unit_index + 1
    prepared.cells[row][0] = result.label
    for i, name in enumerate(prepared.field_names):
        raw = result.fields.get(name, "") or ""
        display, title = format_extracted_field(name, raw)
        prepared.cells[row][i + 1] = display


def finalize_extraction(
    prepared: PreparedExtraction,
    output_path: str | None = None,
) -> str:
    """추출 결과를 엑셀 파일로 저장한다."""
    if output_path is None:
        if prepared.output_path:
            output_path = prepared.output_path
        else:
            fd, output_path = tempfile.mkstemp(suffix=".xlsx", prefix="extract_")
            os.close(fd)
    write_extraction_excel(output_path, prepared.field_names, prepared.items)
    prepared.output_path = output_path
    return output_path


def build_preview_response(session_id: str, prepared: PreparedExtraction) -> dict:
    """업로드 직후 화면 표시용 JSON."""
    data_rows = list(range(2, len(prepared.cells) + 1))
    return {
        "session_id": session_id,
        "field_names": prepared.field_names,
        "format_suggestions": {
            name: suggest_field_format(name) for name in prepared.field_names
        },
        "target_columns": prepared.field_col_indices(),
        "download_filename": prepared.download_filename(),
        "header_row": 1,
        "n_rows": len(prepared.cells),
        "n_cols": len(prepared.cells[0]) if prepared.cells else 0,
        "cells": prepared.cells,
        "extract_rows": data_rows,
    }


def extraction_summary(prepared: PreparedExtraction) -> str:
    ok = sum(1 for i in prepared.items if not i.error)
    total = len(prepared.items)
    return f"전체 {total}건 / 성공 {ok} / 오류 {total - ok}"


def list_doc_types() -> list[dict[str, str | list[str]]]:
    """UI/API용 문서 유형 목록."""
    from app.schemas.extraction import DOC_TYPE_PRESETS

    return [
        {
            "key": preset.key,
            "label": preset.label,
            "fields": list(preset.field_names),
        }
        for preset in DOC_TYPE_PRESETS.values()
    ]


def _is_allowed_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _ALLOWED_SUFFIXES


def _list_allowed_files(root: Path) -> list[Path]:
    skip_parts = {"__MACOSX"}
    return sorted(
        p
        for p in root.rglob("*")
        if _is_allowed_file(p)
        and not any(part.startswith(".") or part in skip_parts for part in p.parts)
    )


def _extract_zip(zip_path: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise FieldExtractionError(f"올바른 ZIP 파일이 아닙니다: {zip_path.name}") from exc


def parse_target_fields(text: str) -> list[str]:
    """쉼표로 구분된 추출 항목 이름 목록."""
    names = [c.strip() for c in text.split(",") if c and c.strip()]
    if not names:
        raise FieldExtractionError("추출할 항목 이름을 한 개 이상 입력해 주세요.")
    return names


def resolve_doc_hint(doc_type: str | None, field_names: list[str] | None = None) -> str:
    names = field_names or []
    if doc_type and doc_type.strip():
        key = doc_type.strip()
        if key == "mixed_contract" and names:
            return build_mixed_doc_hint(names)
        preset = get_doc_type_preset(key)
        return preset.doc_hint if preset else ""
    if names and has_mixed_field_types(names):
        return build_mixed_doc_hint(names)
    return ""


def prepare_zip_root(zip_path: str) -> Path:
    """ZIP 하나를 풀어 작업 루트를 만든다."""
    path = Path(zip_path)
    if path.suffix.lower() != ".zip":
        raise FieldExtractionError("ZIP 파일을 업로드해 주세요.")
    root = Path(tempfile.mkdtemp(prefix="extract_zip_"))
    _extract_zip(path, root)
    if not _list_allowed_files(root):
        raise FieldExtractionError("ZIP 안에서 서류 이미지를 찾지 못했습니다.")
    return root


def group_document_units(root: Path, batch_mode: str) -> list[DocumentUnit]:
    """일괄 추출 모드에 따라 서류 단위 목록을 만든다."""
    if batch_mode not in BATCH_MODES:
        raise FieldExtractionError(f"알 수 없는 일괄 모드입니다: {batch_mode}")

    files = _list_allowed_files(root)
    if not files:
        raise FieldExtractionError("추출할 서류 파일을 찾지 못했습니다.")

    if batch_mode == BATCH_SINGLE:
        return [DocumentUnit(label="업로드 서류", folder_path=str(root))]

    if batch_mode == BATCH_PER_FILE:
        units: list[DocumentUnit] = []
        for i, file_path in enumerate(files, start=1):
            unit_dir = root / f"_unit_{i}"
            unit_dir.mkdir(exist_ok=True)
            shutil.copy2(file_path, unit_dir / file_path.name)
            units.append(DocumentUnit(label=file_path.name, folder_path=str(unit_dir)))
        return units

    # per_folder: 같은 폴더 = 한 서류(앞·뒤). 루트에 파일만 여러 개면 파일마다 분리.
    by_parent: dict[str, list[Path]] = {}
    for file_path in files:
        if file_path.parent == root:
            key = f"__file__:{file_path.name}"
        else:
            key = str(file_path.parent.relative_to(root)).replace("\\", "/")
        by_parent.setdefault(key, []).append(file_path)

    units: list[DocumentUnit] = []
    file_idx = 0
    for key in sorted(by_parent.keys()):
        paths = by_parent[key]
        if key.startswith("__file__:"):
            file_idx += 1
            unit_dir = root / f"_unit_{file_idx}"
            unit_dir.mkdir(exist_ok=True)
            f = paths[0]
            shutil.copy2(f, unit_dir / f.name)
            units.append(DocumentUnit(label=f.name, folder_path=str(unit_dir)))
        else:
            units.append(DocumentUnit(label=key, folder_path=str(root / key)))
    return units


def extract_by_doc_type(folder_path: str, doc_type: str) -> dict[str, str]:
    """문서 유형 프리셋에 따라 폴더 안 서류에서 필드를 추출한다."""
    preset = get_doc_type_preset(doc_type)
    if preset is None:
        raise FieldExtractionError(f"알 수 없는 문서 유형입니다: {doc_type}")

    logger.info(
        "필드 추출 시작 doc_type=%s folder=%s fields=%s",
        preset.key,
        folder_path,
        preset.field_names,
    )
    return extract_fields_from_documents(
        folder_path,
        list(preset.field_names),
        doc_hint=preset.doc_hint,
    )


def _extract_one_unit_fields(
    unit: DocumentUnit,
    field_names: list[str],
    doc_hint: str,
) -> ExtractionItemResult:
    try:
        values = extract_fields_from_documents(
            unit.folder_path,
            field_names,
            doc_hint=doc_hint,
        )
        return ExtractionItemResult(label=unit.label, fields=values)
    except Exception as exc:  # noqa: BLE001
        logger.exception("일괄 추출 실패 label=%s", unit.label)
        empty = {name: "" for name in field_names}
        return ExtractionItemResult(label=unit.label, fields=empty, error=str(exc))


def extract_batch_fields(
    root: Path,
    field_names: list[str],
    batch_mode: str = BATCH_PER_FOLDER,
    *,
    doc_hint: str = "",
) -> list[ExtractionItemResult]:
    """사용자 지정 필드로 여러 서류 단위를 병렬 추출한다."""
    if batch_mode not in BATCH_MODES:
        raise FieldExtractionError(f"알 수 없는 일괄 모드입니다: {batch_mode}")

    units = group_document_units(root, batch_mode)
    if not units:
        raise FieldExtractionError("추출할 서류를 찾지 못했습니다.")

    logger.info(
        "일괄 추출 시작 fields=%s mode=%s units=%d",
        field_names,
        batch_mode,
        len(units),
    )

    if len(units) == 1:
        return [_extract_one_unit_fields(units[0], field_names, doc_hint)]

    max_workers = max(1, min(get_settings().max_parallel_rows, len(units)))
    results: list[ExtractionItemResult | None] = [None] * len(units)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_extract_one_unit_fields, unit, field_names, doc_hint): i
            for i, unit in enumerate(units)
        }
        for fut in as_completed(future_map):
            idx = future_map[fut]
            results[idx] = fut.result()

    return [r for r in results if r is not None]


def write_extraction_excel(
    output_path: str,
    field_names: list[str],
    items: list[ExtractionItemResult],
) -> str:
    """추출 결과를 엑셀 한 파일로 저장한다."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "추출결과"
    ws.append(["서류경로", *field_names])
    for item in items:
        ws.append(
            [
                item.label,
                *[
                    format_extracted_field(name, item.fields.get(name, "") or "")[0]
                    for name in field_names
                ],
            ]
        )
    wb.save(output_path)
    return output_path


def run_zip_extraction(
    zip_path: str,
    target_fields: str,
    *,
    doc_type: str = "",
    batch_mode: str = BATCH_PER_FOLDER,
    output_path: str | None = None,
) -> tuple[str, list[ExtractionItemResult]]:
    """ZIP 일괄 추출 후 엑셀 경로와 결과 목록을 반환한다."""
    field_names = parse_target_fields(target_fields)
    doc_hint = resolve_doc_hint(doc_type or None, field_names)
    root = prepare_zip_root(zip_path)
    items = extract_batch_fields(root, field_names, batch_mode, doc_hint=doc_hint)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".xlsx", prefix="extract_")
        os.close(fd)

    write_extraction_excel(output_path, field_names, items)
    return output_path, items
