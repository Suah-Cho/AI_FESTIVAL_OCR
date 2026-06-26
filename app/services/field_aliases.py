"""필드 이름 동의어 (엑셀 헤더·LLM 추출·후처리 공통).

사용자/엑셀에서 쓰는 다양한 열 이름을 같은 의미로 묶는다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

RRN_INVALID_DISPLAY = "(확인필요)"

# 주민등록번호: YYMMDD-XXXXXXX (하이픈 포함 13자리)
_RE_RRN_STRICT = re.compile(r"^\d{6}-\d{7}$")
# canonical 이름 -> 동의어(정규화 전 원문)
FIELD_GROUPS: dict[str, frozenset[str]] = {
    "주민등록번호": frozenset(
        {
            "주민등록번호",
            "주민번호",
            "실명번호",
            "주민등록번호(실명번호)",
            "주민등록(실명)번호",
        }
    ),
    "파일폴더": frozenset(
        {
            "파일폴더",
            "폴더명",
            "폴더",
            "파일폴더명",
            "거래처폴더",
            "그룹폴더",
            "업체폴더",
            "매장폴더",
            "점포폴더",
            "법인폴더",
            "그룹명",
        }
    ),
    "파일번호": frozenset(
        {
            "파일번호",
            "검증파일",
            "파일No",
            "파일NO",
            "파일no",
            "서류번호",
            "문서번호",
            "파일ID",
            "파일id",
            "일련번호",
            "관리번호",
            "케이스번호",
            "건수번호",
            "점포코드",
            "매장코드",
            "점포번호",
            "매장번호",
            "가맹점번호",
            "가맹점코드",
        }
    ),
    "서류경로": frozenset(
        {
            "서류경로",
            "문서경로",
            "경로",
            "폴더경로",
            "파일경로",
            "서류폴더경로",
            "문서폴더경로",
            "서류위치",
            "문서위치",
        }
    ),
}

LOCATION_GROUP_NAMES = ("파일폴더", "파일번호", "서류경로")


@dataclass(frozen=True)
class LocationColumnResolution:
    """서류 폴더를 가리키는 엑셀 열 해석 결과."""

    mode: str  # "two" | "path" | "auto"
    folder_col: int | None = None
    file_no_col: int | None = None
    path_col: int | None = None


def normalize_field_name(name: str) -> str:
    """필드 이름 비교용 정규화."""
    return unicodedata.normalize("NFKC", str(name)).strip().replace(" ", "")


def field_canonical(name: str) -> str | None:
    """동의어 그룹이 있으면 대표(canonical) 이름, 없으면 None."""
    norm = normalize_field_name(name)
    for canonical, aliases in FIELD_GROUPS.items():
        if norm in {normalize_field_name(a) for a in aliases}:
            return canonical
    return None


def field_aliases(name: str) -> frozenset[str]:
    """해당 필드와 같은 의미로 취급할 이름 목록."""
    canonical = field_canonical(name)
    if canonical is not None:
        return FIELD_GROUPS[canonical]
    return frozenset({name})


def is_rrn_field(name: str) -> bool:
    return field_canonical(name) == "주민등록번호"


def is_birth_date_field(name: str) -> bool:
    return "생년월일" in str(name).strip()


def normalize_rrn_value(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"\s", "", text)


def is_valid_rrn_format(value: Optional[str]) -> bool:
    """주민등록번호 형식 YYMMDD-XXXXXXX (7자리) 인지 검사한다."""
    cleaned = normalize_rrn_value(value)
    if not cleaned:
        return False
    return bool(_RE_RRN_STRICT.match(cleaned))


def format_extracted_field(field_name: str, value: Optional[str]) -> tuple[str, str]:
    """추출값 표시용 (화면·엑셀). (표시문구, 툴팁/원본)"""
    raw = str(value or "").strip()
    if not raw:
        return "(없음)", ""
    if is_rrn_field(field_name) and not is_valid_rrn_format(raw):
        return RRN_INVALID_DISPLAY, raw
    return raw, raw


def expanded_header_norms(names: list[str]) -> set[str]:
    """헤더 행 탐색 시 동의어까지 포함한 정규화 이름 집합."""
    wanted: set[str] = set()
    for name in names:
        for alias in field_aliases(name):
            norm = normalize_field_name(alias)
            if norm:
                wanted.add(norm)
    return wanted


def find_column_index(column_index: dict[str, int], name: str) -> Optional[int]:
    """정규화 헤더 dict 에서 이름(동의어 포함)으로 열 번호를 찾는다."""
    for alias in field_aliases(name):
        col = column_index.get(normalize_field_name(alias))
        if col is not None:
            return col
    return None


def all_location_header_names() -> list[str]:
    """헤더 행 탐색·서류 위치 열 자동 인식용 동의어 목록."""
    names: list[str] = []
    for canonical in LOCATION_GROUP_NAMES:
        names.extend(FIELD_GROUPS[canonical])
    return names


def find_column_for_group(
    column_index: dict[str, int],
    raw_headers: dict[int, str],
    canonical: str,
) -> Optional[int]:
    """동의어 그룹(canonical)에 해당하는 열 번호를 찾는다."""
    for alias in FIELD_GROUPS.get(canonical, frozenset()):
        col = column_index.get(normalize_field_name(alias))
        if col is not None:
            return col
    for col, raw in raw_headers.items():
        if field_canonical(raw) == canonical:
            return col
    return None


def split_doc_path(path: str) -> tuple[str, str]:
    """``이마트24_2/157`` 형태 경로를 (파일폴더, 파일번호)로 나눈다."""
    text = path.strip().replace("\\", "/")
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return "", parts[0]
    return "", ""


def resolve_location_columns(
    column_index: dict[str, int],
    raw_headers: dict[int, str],
    folder_hint: str,
    file_no_hint: str,
) -> LocationColumnResolution:
    """서류 위치 열을 동의어·단일 경로·ZIP 자동 매칭 순으로 해석한다."""
    folder_col = find_column_index(column_index, folder_hint) or find_column_for_group(
        column_index, raw_headers, "파일폴더"
    )
    file_no_col = find_column_index(column_index, file_no_hint) or find_column_for_group(
        column_index, raw_headers, "파일번호"
    )
    path_col = find_column_for_group(column_index, raw_headers, "서류경로")

    if folder_col and file_no_col and folder_col != file_no_col:
        return LocationColumnResolution(
            mode="two", folder_col=folder_col, file_no_col=file_no_col
        )
    if path_col is not None:
        return LocationColumnResolution(mode="path", path_col=path_col)
    if folder_col is not None and file_no_col is None:
        return LocationColumnResolution(mode="path", path_col=folder_col)
    return LocationColumnResolution(mode="auto")


def get_field_value(fields: dict, name: str) -> str:
    """추출 결과 dict 에서 이름(동의어 키 포함)으로 값을 찾는다."""
    direct = fields.get(name)
    if direct is not None and str(direct).strip():
        return str(direct).strip()

    canonical = field_canonical(name)
    if canonical is None:
        return str(fields.get(name, "") or "").strip()

    for key, value in fields.items():
        if field_canonical(key) == canonical and value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def build_field_alias_hints(field_names: list[str]) -> str:
    """LLM 프롬프트에 붙일 동의어 설명."""
    lines: list[str] = []
    for name in field_names:
        if is_rrn_field(name) and normalize_field_name(name) != normalize_field_name(
            "주민등록번호"
        ):
            lines.append(
                f"- '{name}': 신분증(주민등록증·운전면허증 등)의 **주민등록번호**(실명번호). "
                "형식은 YYMMDD-XXXXXXX (하이픈 포함 13자리). "
                "서류에 적힌 그대로 추출하십시오."
            )
        elif is_birth_date_field(name):
            lines.append(
                f"- '{name}': 생년월일. 신분증에서 먼저 확인하되, "
                "판독이 어려우면 사업자등록증의 대표자 생년월일 등을 참고하십시오."
            )
    if not lines:
        return ""
    return "항목 이름 안내:\n" + "\n".join(lines)
