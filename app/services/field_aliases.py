"""필드 이름 동의어 (엑셀 헤더·LLM 추출·후처리 공통).

사용자/엑셀에서 쓰는 다양한 열 이름을 같은 의미로 묶는다.
"""

from __future__ import annotations

import re
import unicodedata
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
}


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
    if not lines:
        return ""
    return "항목 이름 안내:\n" + "\n".join(lines)
