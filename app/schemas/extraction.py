"""문서 유형별 정보 추출 스키마."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocTypePreset:
    """서류 종류별 추출 필드 프리셋."""

    key: str
    label: str
    field_names: tuple[str, ...]
    doc_hint: str


DOC_TYPE_PRESETS: dict[str, DocTypePreset] = {
    "business_registration": DocTypePreset(
        key="business_registration",
        label="사업자등록증",
        field_names=(
            "대표자 전화번호",
            "대표 전화번호",
            "개업연월일",
        ),
        doc_hint=(
            "첨부 이미지는 사업자등록증입니다.\n"
            "- '대표자 전화번호': 대표자(개인) 휴대전화 또는 연락처\n"
            "- '대표 전화번호': 사업장/회사 대표 전화번호\n"
            "- '개업연월일': 개업연월일 또는 사업개시일 (서류에 적힌 그대로)\n"
        ),
    ),
    "id_card": DocTypePreset(
        key="id_card",
        label="신분증",
        field_names=(
            "국적",
            "성명",
            "성별",
            "생년월일",
            "주소",
            "주민등록번호",
        ),
        doc_hint=(
            "첨부 이미지는 신분증(주민등록증, 운전면허증, 외국인등록증 등)입니다.\n"
            "- '국적': 주민등록증·운전면허증 등 국내 발급 신분증은 "
            "국적란이 없을 수 있음. 주소가 국내(서울·경기 등 시·도·구·동)이면 '대한민국'. "
            "외국인등록증은 서류에 적힌 국가명 그대로.\n"
            "- '성명': 이름 전체\n"
            "- '성별': 서류에 없으면 주민등록번호 7번째 자리로 판별 "
            "(1,3,5,7,9=남 / 2,4,6,8,0=여)\n"
            "- '생년월일': YYYY-MM-DD 또는 서류 표기 그대로\n"
            "- '주소': 거주지 주소 전체\n"
            "- '주민등록번호': 주민등록번호 전체 (마스킹 없이 서류에 보이는 그대로)\n"
        ),
    ),
}


def get_doc_type_preset(doc_type: str) -> DocTypePreset | None:
    return DOC_TYPE_PRESETS.get(doc_type.strip())
