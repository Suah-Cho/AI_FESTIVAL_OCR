"""엑셀 헤더 탐색 및 셀 색칠 유틸리티.

- 헤더 행 위치를 고정하지 않고, 입력한 열 이름들이 가장 많이 발견되는 행을
  헤더 행으로 판단한다.
- 검증 대상 열의 셀 자체에 색을 칠한다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from openpyxl.styles import PatternFill
from openpyxl.worksheet.worksheet import Worksheet

# 색상 정의 (요구사항)
FILL_MISMATCH = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # 빨강
FILL_NEEDS_CHECK = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")  # 노랑
FILL_MATCH = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # 초록
FILL_EXCLUDED = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")  # 회색

# 헤더 탐색 시 살펴볼 최대 행 수
MAX_HEADER_SCAN_ROWS = 30


def _norm_header(text: Optional[str]) -> str:
    """헤더 비교용 정규화: 유니코드 정규화 + 공백 제거."""
    if text is None:
        return ""
    return unicodedata.normalize("NFKC", str(text)).strip().replace(" ", "")


@dataclass
class HeaderLayout:
    """탐색된 헤더 행 정보."""

    header_row: int
    column_index: dict[str, int] = field(default_factory=dict)  # 정규화이름 -> 열(1-base)
    raw_headers: dict[int, str] = field(default_factory=dict)  # 열 -> 원본 헤더 텍스트

    def find_column(self, name: str) -> Optional[int]:
        return self.column_index.get(_norm_header(name))


def detect_header_row(ws: Worksheet, candidate_names: list[str]) -> Optional[HeaderLayout]:
    """후보 열 이름들이 가장 많이 매칭되는 행을 헤더 행으로 판단한다."""
    wanted = {_norm_header(n) for n in candidate_names if _norm_header(n)}
    if not wanted:
        return None

    best_row = None
    best_hits = 0
    scan_limit = min(ws.max_row, MAX_HEADER_SCAN_ROWS)
    for row_idx in range(1, scan_limit + 1):
        hits = sum(1 for cell in ws[row_idx] if _norm_header(cell.value) in wanted)
        if hits > best_hits:
            best_hits = hits
            best_row = row_idx

    if best_row is None or best_hits == 0:
        return None

    layout = HeaderLayout(header_row=best_row)
    for cell in ws[best_row]:
        norm = _norm_header(cell.value)
        if norm and norm not in layout.column_index:
            layout.column_index[norm] = cell.column
            layout.raw_headers[cell.column] = str(cell.value).strip()
    return layout


def resolve_columns(
    layout: HeaderLayout, target_columns: list[str]
) -> tuple[dict[str, int], list[str]]:
    """입력한 열 이름들을 실제 열 인덱스로 매핑한다.

    반환: (이름->열인덱스 매핑, 못 찾은 이름 목록)
    """
    resolved: dict[str, int] = {}
    missing: list[str] = []
    for name in target_columns:
        col = layout.find_column(name)
        if col is None:
            missing.append(name)
        else:
            resolved[name] = col
    return resolved, missing


def append_result_column(ws: Worksheet, layout: HeaderLayout, title: str = "검증결과") -> int:
    """헤더 행 맨 끝에 결과 열을 추가하고 그 열 인덱스를 반환한다.

    이미 같은 제목 열이 있으면 그 열을 재사용한다.
    """
    existing = layout.find_column(title)
    if existing is not None:
        return existing
    new_col = ws.max_column + 1
    ws.cell(row=layout.header_row, column=new_col, value=title)
    return new_col


def fill_cell(ws: Worksheet, row: int, col: int, fill: PatternFill) -> None:
    ws.cell(row=row, column=col).fill = fill
