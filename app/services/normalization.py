"""값 정규화 및 비교 모듈.

원칙: 비교/판정은 코드가 담당한다. (LLM에게 비교를 맡기지 않는다.)

정규화 규칙은 '열 이름'이 아니라 '값의 형태'를 보고 자동으로 적용한다.
새 규칙은 ``NORMALIZERS`` 리스트에 추가하면 되며, 위에서부터 먼저 매칭되는
규칙 하나가 적용된다. 어떤 규칙에도 해당하지 않으면 공통 정규화만 적용된다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

# 판정 결과 상수
MATCH = "일치"
MISMATCH = "불일치"
NEEDS_CHECK = "확인필요"
EXCLUDED = "제외"


def _common_clean(value: Optional[str]) -> str:
    """공통 기본 정규화: 유니코드 정규화 + 양끝 공백 제거."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return text.strip()


_RE_DIGITS = re.compile(r"\d")
_RE_NON_DIGIT = re.compile(r"\D")

# 날짜로 볼 만한 패턴: 2021.10.08 / 2021-10-08 / 2021/10/08 / 20211008 / 2021년 10월 8일
_RE_DATE_LIKE = re.compile(
    r"^\s*\d{4}\s*[.\-/년]?\s*\d{1,2}\s*[.\-/월]?\s*\d{1,2}\s*[일]?\s*$"
)
# 사업자번호/숫자 위주(하이픈, 공백 허용)
_RE_NUMBERISH = re.compile(r"^[\d\s\-().]+$")

_COMPANY_NOISE = [
    "주식회사",
    "(주)",
    "（주）",
    "㈜",
    "유한회사",
    "(유)",
]


def _normalize_number(value: str) -> str:
    """숫자성 값: 숫자만 남긴다. (사업자번호 등)"""
    return _RE_NON_DIGIT.sub("", _common_clean(value))


def _normalize_date(value: str) -> str:
    """날짜성 값: 구분자 제거하고 YYYYMMDD 8자리로 통일.

    20211008 == 2021.10.08 == 2021-10-08 가 모두 같게 처리된다.
    """
    cleaned = _common_clean(value)
    tokens = re.split(r"[.\-/년월일\s]+", cleaned)
    tokens = [t for t in tokens if t.isdigit()]
    if len(tokens) >= 3 and len(tokens[0]) == 4:
        y, m, d = tokens[0], tokens[1], tokens[2]
        return f"{y}{int(m):02d}{int(d):02d}"
    return _RE_NON_DIGIT.sub("", cleaned)


def _normalize_name(value: str) -> str:
    """상호/이름성 값: 회사 표기와 공백/특수문자를 제거한다."""
    text = _common_clean(value)
    for noise in _COMPANY_NOISE:
        text = text.replace(noise, "")
    return re.sub(r"[\s().,\-_/]", "", text)


def _normalize_default(value: str) -> str:
    """기본 정규화: 공백/일반 특수문자 정리 + 소문자화."""
    text = _common_clean(value)
    text = re.sub(r"[\s().,\-_/]", "", text)
    return text.lower()


@dataclass
class NormalizeRule:
    name: str
    detector: Callable[[str], bool]
    normalizer: Callable[[str], str]


def _looks_like_date(value: str) -> bool:
    return bool(_RE_DATE_LIKE.match(_common_clean(value)))


def _looks_like_number(value: str) -> bool:
    cleaned = _common_clean(value)
    if not cleaned or not _RE_NUMBERISH.match(cleaned):
        return False
    return len(_RE_NON_DIGIT.sub("", cleaned)) >= 4


def _looks_like_name(value: str) -> bool:
    text = _common_clean(value)
    if any(noise in text for noise in _COMPANY_NOISE):
        return True
    return bool(text) and not _RE_DIGITS.search(text)


# 위에서부터 먼저 매칭되는 규칙 하나가 적용된다.
NORMALIZERS: list[NormalizeRule] = [
    NormalizeRule("date", _looks_like_date, _normalize_date),
    NormalizeRule("number", _looks_like_number, _normalize_number),
    NormalizeRule("name", _looks_like_name, _normalize_name),
]


def normalize_value(value: Optional[str], paired_value: Optional[str] = None) -> str:
    """값의 형태에 맞는 정규화를 자동 선택해서 적용한다.

    paired_value 가 주어지면 두 값 중 하나라도 특정 형태로 보이면 동일 규칙을
    적용해, 한쪽이 모호해도 같은 기준으로 비교되도록 한다.
    """
    text = _common_clean(value)
    if not text:
        return ""

    for rule in NORMALIZERS:
        if rule.detector(text) or (
            paired_value and rule.detector(_common_clean(paired_value))
        ):
            return rule.normalizer(text)
    return _normalize_default(text)


def compare_values(expected: Optional[str], extracted: Optional[str]) -> str:
    """기준값(expected, 엑셀)과 추출값(extracted, 서류)을 비교해 판정한다.

    반환: MATCH / MISMATCH / NEEDS_CHECK
    """
    exp_raw = _common_clean(expected)
    ext_raw = _common_clean(extracted)

    if not ext_raw:
        return NEEDS_CHECK

    exp_norm = normalize_value(exp_raw, ext_raw)
    ext_norm = normalize_value(ext_raw, exp_raw)

    if not exp_norm and not ext_norm:
        return NEEDS_CHECK
    return MATCH if exp_norm == ext_norm else MISMATCH
