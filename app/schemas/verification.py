"""검증 관련 데이터 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CellEdit(BaseModel):
    row: int
    col: int
    value: str = ""


class CellEditsRequest(BaseModel):
    edits: list[CellEdit] = Field(default_factory=list)


class VerifyResult(BaseModel):
    """검증 결과 요약 + 생성된 결과 엑셀 경로."""

    output_path: str
    total_rows: int = 0
    verified_rows: int = 0
    excluded_rows: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    missing_columns: list[str] = Field(default_factory=list)

    def summary_text(self) -> str:
        c = self.counts
        return (
            f"전체 {self.total_rows}행 / 검증 {self.verified_rows} / 제외 {self.excluded_rows} · "
            f"일치 {c.get('일치', 0)}, 불일치 {c.get('불일치', 0)}, 확인필요 {c.get('확인필요', 0)}"
        )
