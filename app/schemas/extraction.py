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
    "mixed_contract": DocTypePreset(
        key="mixed_contract",
        label="사업자+신분증 (혼합)",
        field_names=(
            "대표자명",
            "상호명",
            "사업자번호",
            "실명번호",
            "휴대전화",
            "성별",
            "생년월일",
            "국적",
        ),
        doc_hint=(
            "첨부 이미지에는 사업자등록증·신분증(주민등록증 등)이 함께 있을 수 있습니다.\n"
            "각 항목은 지정한 서류에서만 찾으십시오. 다른 서류의 번호를 섞지 마십시오.\n\n"
            "【사업자등록증】에서 추출:\n"
            "- 대표자명, 상호명, 사업자번호\n"
            "- 사업자번호는 000-00-00000 형식. 계좌번호·통장번호와 혼동하지 마십시오.\n\n"
            "【신분증】에서 추출:\n"
            "- 실명번호(주민등록번호): YYMMDD-XXXXXXX (하이픈 포함 13자리)\n"
            "- 성별, 생년월일, 국적\n"
            "- 신분증에 없는 항목은 빈 문자열.\n\n"
            "【신청서·계약서·사업자등록증 등】에서 추출:\n"
            "- 휴대전화: 서류에 적힌 휴대전화. 신분증에는 없을 수 있음.\n"
        ),
    ),
    "business_registration": DocTypePreset(
        key="business_registration",
        label="사업자등록증",
        field_names=(
            "대표자명",
            "상호명",
            "사업자번호",
            "대표자 전화번호",
            "대표 전화번호",
            "개업연월일",
        ),
        doc_hint=(
            "첨부 이미지는 사업자등록증입니다.\n"
            "- '대표자명'·'대표자': 대표자 성명\n"
            "- '상호명'·'상호': 사업장 상호\n"
            "- '사업자번호': 사업자등록번호 (000-00-00000 형식)\n"
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
            "- '주민등록번호': 주민등록번호(실명번호) 전체. 열 이름이 '실명번호'인 경우에도 동일\n"
        ),
    ),
}


def get_doc_type_preset(doc_type: str) -> DocTypePreset | None:
    return DOC_TYPE_PRESETS.get(doc_type.strip())
