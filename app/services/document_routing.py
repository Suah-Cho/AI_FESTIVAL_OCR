"""서류 파일·필드 이름을 종류별로 분류해 분리 추출에 사용한다."""

from __future__ import annotations

from pathlib import Path

from app.schemas.extraction import get_doc_type_preset
from app.services.field_aliases import is_rrn_field, normalize_field_name

DOC_TYPE_BUSINESS = "business_registration"
DOC_TYPE_ID = "id_card"
DOC_TYPE_CONTACT = "contact"
DOC_TYPE_FALLBACK = "fallback"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_PDF_EXTS = {".pdf"}

# 파일명(및 상위 폴더명) 키워드 → 서류 종류 (앞쪽 규칙 우선)
_FILE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DOC_TYPE_BUSINESS, ("사업자", "business", "bizreg", "biz_reg", "biz")),
    (
        DOC_TYPE_ID,
        (
            "신분",
            "주민",
            "idcard",
            "id_card",
            "면허",
            "운전",
            "여권",
            "passport",
            "identification",
        ),
    ),
)

CONTACT_DOC_HINT = (
    "첨부 이미지 중 신청서·계약서·사업자등록증 등에서 연락처를 찾으십시오.\n"
    "- '휴대전화'·'휴대폰': 휴대전화 번호 (010 등). 신분증에는 없을 수 있음.\n"
    "- '전화'·'대표자 전화': 서류에 적힌 해당 번호.\n"
)


def list_image_files(folder_path: str) -> list[Path]:
    """폴더 안 OCR 대상 이미지 목록."""
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    skip = {"__MACOSX"}
    return sorted(
        p
        for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in _IMAGE_EXTS
        and not any(part.startswith(".") or part in skip for part in p.parts)
    )


def classify_file(path: Path) -> str:
    """파일·부모 폴더 이름으로 서류 종류를 추정한다."""
    haystack = normalize_field_name(f"{path.parent.name}/{path.stem}").lower()
    for doc_type, keywords in _FILE_RULES:
        for kw in keywords:
            if kw in haystack:
                return doc_type
    return DOC_TYPE_FALLBACK


def group_files_by_doc_type(folder_path: str) -> dict[str, list[Path]]:
    """폴더 안 이미지를 서류 종류별로 묶는다."""
    groups: dict[str, list[Path]] = {
        DOC_TYPE_BUSINESS: [],
        DOC_TYPE_ID: [],
        DOC_TYPE_FALLBACK: [],
    }
    for path in list_image_files(folder_path):
        groups[classify_file(path)].append(path)
    return groups


def doc_type_for_field(field_name: str) -> str:
    """추출 항목 이름이 어느 서류에서 나와야 하는지 추정한다."""
    name = field_name.strip()
    norm = normalize_field_name(name)

    if is_rrn_field(name):
        return DOC_TYPE_ID
    if norm in {"성명", "이름"}:
        return DOC_TYPE_ID
    if any(k in name for k in ("성별", "생년월일", "국적")) and "대표" not in name:
        return DOC_TYPE_ID
    if "주소" in name and "사업장" not in name and "사업" not in name:
        return DOC_TYPE_ID

    if any(k in name for k in ("사업자", "상호", "개업", "법인", "업태", "종목", "사업장")):
        return DOC_TYPE_BUSINESS
    if "대표자" in name:
        return DOC_TYPE_BUSINESS

    if any(k in name for k in ("휴대", "핸드폰", "휴대전화", "mobile", "cellphone")):
        return DOC_TYPE_CONTACT
    if "전화" in name:
        return DOC_TYPE_CONTACT

    return DOC_TYPE_FALLBACK


def group_fields_by_doc_type(field_names: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name in field_names:
        groups.setdefault(doc_type_for_field(name), []).append(name)
    return groups


def has_mixed_field_types(field_names: list[str]) -> bool:
    """추출 항목이 사업자등록증·신분증 등 여러 서류 종류를 아우르는지."""
    groups = group_fields_by_doc_type(field_names)
    types = {k for k, v in groups.items() if v and k != DOC_TYPE_FALLBACK}
    return len(types) >= 2


def build_mixed_doc_hint(field_names: list[str]) -> str:
    """항목 목록에 맞춰 '사업자등록증에서 ○○, 신분증에서 △△' 안내를 만든다."""
    header = (
        "첨부 이미지에는 사업자등록증·신분증(주민등록증·운전면허증 등)이 "
        "함께 있을 수 있습니다. 각 항목은 아래 지정한 서류에서만 찾으십시오. "
        "계좌번호·사업자번호를 실명번호(주민등록번호)로 넣지 마십시오."
    )
    groups = group_fields_by_doc_type(field_names)
    lines: list[str] = [header, ""]

    business = groups.get(DOC_TYPE_BUSINESS, [])
    if business:
        lines.append(f"【사업자등록증】에서만 추출: {', '.join(business)}")
        lines.append(
            "- 대표자명·상호명·사업자번호는 사업자등록증에 적힌 값만 사용하십시오."
        )
        lines.append("")

    id_fields = groups.get(DOC_TYPE_ID, [])
    if id_fields:
        lines.append(f"【신분증】에서만 추출: {', '.join(id_fields)}")
        lines.append(
            "- 실명번호(주민등록번호)는 YYMMDD-XXXXXXX 형식(하이픈 포함 13자리)."
        )
        lines.append("- 성별·생년월일·국적은 신분증에 있는 값만 사용하십시오.")
        lines.append("")

    contact = groups.get(DOC_TYPE_CONTACT, [])
    if contact:
        lines.append(
            f"【신청서·계약서·사업자등록증 등】에서 연락처 추출: {', '.join(contact)}"
        )
        lines.append("- 휴대전화는 신분증에 없을 수 있습니다.")
        lines.append("")

    fallback = groups.get(DOC_TYPE_FALLBACK, [])
    if fallback:
        lines.append(f"【서류 전체】에서 추출: {', '.join(fallback)}")
        lines.append("")

    return "\n".join(lines).strip()


def should_split_extraction(field_names: list[str], folder_path: str) -> bool:
    """필드·파일이 여러 서류 종류로 나뉘면 분리 추출이 유리하다."""
    field_groups = group_fields_by_doc_type(field_names)
    file_groups = group_files_by_doc_type(folder_path)
    field_types = {k for k, v in field_groups.items() if v and k != DOC_TYPE_FALLBACK}
    if len(field_types) < 2:
        return False
    classified_files = file_groups[DOC_TYPE_BUSINESS] or file_groups[DOC_TYPE_ID]
    return bool(classified_files)


def resolve_files_for_group(
    doc_type: str,
    file_groups: dict[str, list[Path]],
    all_images: list[Path],
) -> list[Path]:
    """해당 서류 종류 추출에 쓸 이미지 목록."""
    if doc_type == DOC_TYPE_CONTACT:
        return all_images
    paths = file_groups.get(doc_type, [])
    if paths:
        return paths
    unknown = file_groups.get(DOC_TYPE_FALLBACK, [])
    if unknown:
        return unknown
    return all_images


def doc_hint_for_group(doc_type: str, base_hint: str = "") -> str:
    """서류 종류별 LLM 안내 문구."""
    parts: list[str] = []
    if doc_type == DOC_TYPE_CONTACT:
        parts.append(CONTACT_DOC_HINT)
    else:
        preset = get_doc_type_preset(doc_type)
        if preset:
            parts.append(preset.doc_hint)
    if base_hint.strip():
        parts.append(base_hint.strip())
    return "\n".join(parts)
