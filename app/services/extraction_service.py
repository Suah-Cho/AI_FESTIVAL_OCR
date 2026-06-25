"""서류 이미지에서 필드 값을 추출하는 서비스.

원칙: "값 추출은 OCR/LLM이" 담당한다. (비교/판정은 normalization.py 가 담당)

정확도를 높이기 위해 여러 모델 슬롯(OCR_MODEL_1, OCR_MODEL_2 ...)을 돌려
필드별 '다수결'로 합칠 수 있다. 슬롯은 특정 벤더에 종속되지 않으며,
``type`` 에 따라 어댑터(호출 방식)가 결정된다. 설정을 바꿔도 검증/판정
로직은 영향을 받지 않는다.

핵심 진입점은 ``extract_fields_from_documents(folder_path, field_names) -> dict`` 이다.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.core.config import OcrModelSpec, get_settings
from app.services.api_transport import chat_completion_json, ocr_post, vaiv_chat_json
from app.services.image_preprocess import read_image_jpeg_bytes
from app.services.normalization import normalize_value

logger = logging.getLogger(__name__)

# 추출기에 보낼 이미지 확장자
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_PDF_EXTS = {".pdf"}


def _list_document_files(folder_path: str) -> list[Path]:
    """폴더 안의 서류 파일(이미지/ PDF) 목록을 반환한다."""
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    return [
        p
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix.lower() in (_IMAGE_EXTS | _PDF_EXTS)
    ]


def has_documents(folder_path: str) -> bool:
    """폴더에 검증 대상 서류 파일이 하나라도 있는지 확인한다."""
    return len(_list_document_files(folder_path)) > 0


def _list_image_files(folder_path: str) -> list[Path]:
    settings = get_settings()
    files = _list_document_files(folder_path)
    return [f for f in files if f.suffix.lower() in _IMAGE_EXTS][
        : settings.max_images_per_folder
    ]


def _encode_image(path: Path) -> Optional[dict]:
    """이미지 파일을 OpenAI 비전 입력용 data URL 형식으로 인코딩한다."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None:
        mime = "image/jpeg"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _build_prompt(
    field_names: list[str], *, doc_hint: str = "", extra_text: str = ""
) -> str:
    fields_str = ", ".join(field_names)
    keys_example = ", ".join(f'"{n}": "..."' for n in field_names)
    prompt = (
        "너는 한국 계약/거래처 서류(사업자등록증, 신분증 등)에서 정보를 추출하는 OCR 도우미야.\n"
    )
    if doc_hint:
        prompt += f"{doc_hint.strip()}\n"
    prompt += (
        f"첨부된 이미지들에서 다음 항목의 값을 찾아줘: {fields_str}\n\n"
        "규칙:\n"
        "- 반드시 JSON 객체 하나만 출력해.\n"
        f"- JSON 키는 정확히 다음과 같아야 해: {{{keys_example}}}\n"
        '- 값을 찾을 수 없으면 빈 문자열("")로 둬.\n'
        "- 추측하지 말고 서류에 적힌 값을 그대로 적어줘.\n"
    )
    if extra_text:
        prompt += (
            "\n참고: 아래는 동일 서류를 다른 OCR 엔진으로 읽은 원문 텍스트야. "
            "글자 판독에 참고하되, 최종 값은 이미지를 기준으로 판단해.\n"
            "--- OCR 원문 시작 ---\n"
            f"{extra_text}\n"
            "--- OCR 원문 끝 ---\n"
        )
    return prompt


def _build_vaiv_system_prompt(field_names: list[str], *, doc_hint: str = "") -> str:
    fields_str = ", ".join(field_names)
    keys_example = ", ".join(f'"{n}": "..."' for n in field_names)
    prompt = (
        "당신은 한국 계약/거래처 서류(사업자등록증, 신분증 등) OCR 및 정보 추출 전문가입니다.\n"
    )
    if doc_hint:
        prompt += f"{doc_hint.strip()}\n"
    prompt += (
        "지침:\n"
        "- 번역하지 마십시오.\n"
        "- 추측하지 마십시오.\n"
        "- 서류에 적힌 값을 그대로 추출하십시오.\n"
        f"- 반드시 JSON 객체 하나만 출력하십시오: {{{keys_example}}}\n"
        '- 값을 찾을 수 없으면 빈 문자열("")로 두십시오.\n'
        f"- JSON 키는 정확히 다음과 같아야 합니다: {fields_str}\n"
    )
    return prompt


def _build_vaiv_user_prompt(field_names: list[str]) -> str:
    fields_str = ", ".join(field_names)
    return (
        f"첨부한 서류 이미지에서 다음 항목의 값을 추출해 주세요: {fields_str}\n"
        "JSON 형식으로만 응답하십시오."
    )


def _encode_images_b64(folder_path: str, *, preprocess: bool) -> list[str]:
    images: list[str] = []
    for img in _list_image_files(folder_path):
        try:
            data = read_image_jpeg_bytes(img, preprocess=preprocess)
        except OSError:
            continue
        images.append(base64.b64encode(data).decode("ascii"))
    return images


# ---------------------------------------------------------------------------
# 더미 추출기 (개발/테스트용)
# ---------------------------------------------------------------------------
def _dummy_extract(folder_path: str, field_names: list[str]) -> dict:
    """OCR 호출 없이 동작 흐름을 확인하기 위한 더미 추출기.

    폴더 안에 ``_mock.json`` 이 있으면 그 내용을 반환하고,
    없으면 모든 필드를 빈 문자열로 반환한다.
    """
    mock_path = Path(folder_path) / "_mock.json"
    if mock_path.is_file():
        try:
            data = json.loads(mock_path.read_text(encoding="utf-8"))
            return {name: str(data.get(name, "")) for name in field_names}
        except (OSError, json.JSONDecodeError):
            pass
    return {name: "" for name in field_names}


# ---------------------------------------------------------------------------
# 어댑터 1: OpenAI 호환 비전 API (gpt-4o / gemini / openrouter / 로컬 vLLM 등)
# ---------------------------------------------------------------------------
def _extract_openai(
    spec: OcrModelSpec,
    folder_path: str,
    field_names: list[str],
    temperature: float,
    *,
    doc_hint: str = "",
) -> dict:
    """OpenAI 호환 비전 모델로 폴더 안 서류에서 값을 추출한다."""
    image_files = _list_image_files(folder_path)
    if not image_files:
        return {name: "" for name in field_names}

    content: list[dict] = [
        {"type": "text", "text": _build_prompt(field_names, doc_hint=doc_hint)}
    ]
    for img in image_files:
        encoded = _encode_image(img)
        if encoded:
            content.append(encoded)

    try:
        data = chat_completion_json(spec, content, temperature)
    except json.JSONDecodeError:
        return {name: "" for name in field_names}
    except Exception as exc:  # API 실패/타임아웃 등
        raise RuntimeError(
            f"OCR_MODEL_{spec.index}({spec.name}) 추출 실패 ({folder_path}): {exc}"
        ) from exc

    return {name: str(data.get(name, "") or "") for name in field_names}


# ---------------------------------------------------------------------------
# 어댑터 3: VAIV /api/chat (prod 운영 OCR, 모델명만 슬롯마다 다름)
# ---------------------------------------------------------------------------
def _extract_vaiv(
    spec: OcrModelSpec,
    folder_path: str,
    field_names: list[str],
    temperature: float,
    *,
    doc_hint: str = "",
) -> dict:
    """VAIV /api/chat API로 폴더 안 서류에서 값을 추출한다."""
    settings = get_settings()
    images_b64 = _encode_images_b64(
        folder_path, preprocess=settings.prod_image_preprocess
    )
    if not images_b64:
        return {name: "" for name in field_names}

    try:
        data = vaiv_chat_json(
            spec,
            system_prompt=_build_vaiv_system_prompt(field_names, doc_hint=doc_hint),
            user_prompt=_build_vaiv_user_prompt(field_names),
            images_b64=images_b64,
            temperature=temperature,
        )
    except json.JSONDecodeError:
        return {name: "" for name in field_names}
    except Exception as exc:
        raise RuntimeError(
            f"OCR_MODEL_{spec.index}({spec.name}) 추출 실패 ({folder_path}): {exc}"
        ) from exc

    return {name: str(data.get(name, "") or "") for name in field_names}


# ---------------------------------------------------------------------------
# 어댑터 2: 전용 OCR(원문 텍스트) + OpenAI 구조화
#   (CLOVA 등 OpenAI 비호환 OCR 엔진을 슬롯으로 끼우는 예시 어댑터)
#   슬롯에 _URL / _SECRET 를 주고 TYPE=clova 로 지정하면 동작한다.
# ---------------------------------------------------------------------------
def _clova_raw_text(spec: OcrModelSpec, folder_path: str) -> str:
    """전용 OCR 엔드포인트로 폴더 안 이미지의 원문 텍스트를 추출해 합쳐 반환한다."""
    settings = get_settings()
    image_files = _list_image_files(folder_path)
    texts: list[str] = []
    for img in image_files:
        ext = img.suffix.lower().lstrip(".")
        fmt = "jpg" if ext in {"jpg", "jpeg"} else ext
        payload = {
            "version": "V2",
            "requestId": "verify",
            "timestamp": 0,
            "images": [{"format": fmt, "name": img.stem}],
        }
        files = {
            "message": (None, json.dumps(payload), "application/json"),
            "file": (img.name, img.read_bytes(), f"image/{fmt}"),
        }
        data = ocr_post(
            url=spec.url,
            secret=spec.secret,
            files=files,
            timeout=settings.openai_timeout,
        )
        for image in data.get("images", []):
            words = [f.get("inferText", "") for f in image.get("fields", [])]
            if words:
                texts.append(" ".join(words))
    return "\n".join(texts)


def _extract_clova(
    spec: OcrModelSpec,
    folder_path: str,
    field_names: list[str],
    temperature: float,
    *,
    doc_hint: str = "",
) -> dict:
    """전용 OCR 원문을 OpenAI에 함께 넣어 필드를 구조화 추출한다.

    구조화(텍스트→필드 매핑)에는 전역 OPENAI_* 설정을 사용한다.
    """
    image_files = _list_image_files(folder_path)
    if not image_files:
        return {name: "" for name in field_names}

    try:
        raw_text = _clova_raw_text(spec, folder_path)
    except Exception as exc:
        raise RuntimeError(
            f"OCR_MODEL_{spec.index}({spec.name}) OCR 실패 ({folder_path}): {exc}"
        ) from exc

    content: list[dict] = [
        {
            "type": "text",
            "text": _build_prompt(field_names, doc_hint=doc_hint, extra_text=raw_text),
        }
    ]
    for img in image_files:
        encoded = _encode_image(img)
        if encoded:
            content.append(encoded)

    settings = get_settings()
    struct_spec = OcrModelSpec(
        index=spec.index,
        type="openai",
        model=settings.openai_vision_model,
        api_key=settings.openai_api_key,
        label=f"{spec.name}+openai",
    )
    try:
        data = chat_completion_json(struct_spec, content, temperature)
    except json.JSONDecodeError:
        return {name: "" for name in field_names}
    except Exception as exc:
        raise RuntimeError(
            f"OCR_MODEL_{spec.index}({spec.name}) 구조화 실패 ({folder_path}): {exc}"
        ) from exc

    return {name: str(data.get(name, "") or "") for name in field_names}


# type -> 어댑터 함수 (spec, folder, fields, temperature) -> dict
_ADAPTERS: dict[str, Callable[[OcrModelSpec, str, list[str], float], dict]] = {
    "openai": _extract_openai,
    "vaiv": _extract_vaiv,
    "clova": _extract_clova,
}


@dataclass
class _Candidate:
    label: str
    data: dict


def _slot_label(spec: OcrModelSpec, sample_index: int) -> str:
    return f"OCR_MODEL_{spec.index}[{spec.type}/{spec.model}]#{sample_index + 1}"


_KOR_SIDO_PREFIXES = (
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
)
_RE_KOR_RRN = re.compile(r"\d{6}-?(\d)")


def _address_field_value(fields: dict) -> str:
    for key in ("주소", "자택 주소", "자택주소", "거주지"):
        val = str(fields.get(key, "") or "").strip()
        if val:
            return val
    return ""


def _looks_like_korean_address(address: str) -> bool:
    text = address.strip()
    if not text:
        return False
    return any(prefix in text for prefix in _KOR_SIDO_PREFIXES)


def _rrn_field_value(fields: dict) -> str:
    return str(fields.get("주민등록번호", "") or fields.get("주민번호", "") or "").strip()


def _rrn_seventh_digit(rrn: str) -> str | None:
    cleaned = re.sub(r"\s", "", rrn)
    m = _RE_KOR_RRN.search(cleaned)
    return m.group(1) if m else None


def _gender_from_rrn_seventh(digit: str) -> str | None:
    """주민번호 7번째 자리: 홀수=남, 짝수=여."""
    if digit in "13579":
        return "남"
    if digit in "02468":
        return "여"
    return None


def _infer_nationality(fields: dict) -> None:
    """국적이 비어 있을 때 국내 주소·내국인 주민번호로 '대한민국'을 보정한다."""
    if str(fields.get("국적", "") or "").strip():
        return

    seventh = _rrn_seventh_digit(_rrn_field_value(fields))
    if seventh in {"5", "6", "7", "8"}:
        return

    if seventh in {"1", "2", "3", "4", "9", "0"}:
        fields["국적"] = "대한민국"
        return

    if _looks_like_korean_address(_address_field_value(fields)):
        fields["국적"] = "대한민국"


def _infer_gender(fields: dict) -> None:
    """성별이 비어 있을 때 주민등록번호 7번째 자리로 남/여를 보정한다."""
    if str(fields.get("성별", "") or "").strip():
        return
    seventh = _rrn_seventh_digit(_rrn_field_value(fields))
    if not seventh:
        return
    gender = _gender_from_rrn_seventh(seventh)
    if gender:
        fields["성별"] = gender


def _apply_field_inferences(fields: dict, field_names: list[str]) -> dict:
    if "국적" in field_names:
        _infer_nationality(fields)
    if "성별" in field_names:
        _infer_gender(fields)
    return fields


def _merge_candidates(candidates: list[_Candidate], field_names: list[str]) -> dict:
    """여러 추출 결과를 필드별 다수결로 병합한다."""
    merged: dict = {}
    for name in field_names:
        votes: dict[str, dict] = {}
        for order, cand in enumerate(candidates):
            raw = str(cand.data.get(name, "") or "").strip()
            if not raw:
                continue
            key = normalize_value(raw) or raw
            slot = votes.get(key)
            if slot is None:
                votes[key] = {"count": 1, "order": order, "raw": raw, "sources": [cand.label]}
            else:
                slot["count"] += 1
                slot["sources"].append(cand.label)
        if not votes:
            merged[name] = ""
            logger.info("다수결 [%s] 후보 없음 → ''", name)
            continue
        best = min(votes.values(), key=lambda v: (-v["count"], v["order"]))
        merged[name] = best["raw"]
        vote_summary = ", ".join(
            f"{v['raw']!r}×{v['count']}({'+'.join(v['sources'])})" for v in votes.values()
        )
        logger.info(
            "다수결 [%s] → %r (득표: %d/%d) | 후보: %s",
            name,
            best["raw"],
            best["count"],
            len(candidates),
            vote_summary,
        )
    return merged


def _real_extract(
    folder_path: str, field_names: list[str], *, doc_hint: str = ""
) -> dict:
    """설정된 모델 슬롯들을 (반복 호출 포함) 돌려 다수결로 합친 결과를 반환한다."""
    settings = get_settings()
    samples = settings.ocr_samples_per_model
    model_list = [
        f"OCR_MODEL_{s.index}[{s.type}/{s.model}]" for s in settings.ocr_models
    ]

    logger.info(
        "추출 시작 env=%s folder=%s fields=%s models=%s samples=%d",
        settings.app_env,
        folder_path,
        field_names,
        model_list,
        samples,
    )

    candidates: list[_Candidate] = []
    last_error: Optional[Exception] = None
    for spec in settings.ocr_models:
        adapter = _ADAPTERS.get(spec.type)
        if adapter is None:
            logger.warning("알 수 없는 어댑터 type=%s slot=%d → 건너뜀", spec.type, spec.index)
            continue
        for i in range(samples):
            label = _slot_label(spec, i)
            temperature = 0.0 if i == 0 else settings.ocr_sample_temperature
            logger.info(
                "모델 호출 시작 %s temp=%.2f folder=%s",
                label,
                temperature,
                folder_path,
            )
            started = time.perf_counter()
            try:
                result = adapter(
                    spec, folder_path, field_names, temperature, doc_hint=doc_hint
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                candidates.append(_Candidate(label=label, data=result))
                logger.info(
                    "모델 호출 성공 %s %.0fms result=%s",
                    label,
                    elapsed_ms,
                    result,
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                last_error = exc
                logger.warning(
                    "모델 호출 실패 %s %.0fms error=%s",
                    label,
                    elapsed_ms,
                    exc,
                )
                continue

    if not candidates:
        logger.error(
            "추출 실패 folder=%s — 모든 모델 호출 실패 (마지막 오류: %s)",
            folder_path,
            last_error,
        )
        if last_error is not None:
            raise RuntimeError(str(last_error))
        return {name: "" for name in field_names}

    if len(candidates) == 1:
        result = dict(candidates[0].data)
        _apply_field_inferences(result, field_names)
        logger.info("단일 모델 결과 사용 folder=%s result=%s", folder_path, result)
        return result

    logger.info("다수결 병합 시작 folder=%s 후보 %d개", folder_path, len(candidates))
    merged = _merge_candidates(candidates, field_names)
    _apply_field_inferences(merged, field_names)
    logger.info("추출 완료 folder=%s merged=%s", folder_path, merged)
    return merged


def extract_fields_from_documents(
    folder_path: str,
    field_names: list[str],
    *,
    doc_hint: str = "",
) -> dict:
    """폴더 안 서류에서 지정한 필드 값을 추출한다.

    설정된 모델 슬롯(OCR_MODEL_N)들을 돌려 필드별 다수결로 합친다.

    Args:
        folder_path: ``{파일폴더}/{파일번호}`` 경로.
        field_names: 추출할 필드 이름 목록 (사용자 입력 = 검증할 열 이름).
        doc_hint: 서류 종류별 추가 안내 (정보 추출 페이지 등).

    Returns:
        {필드이름: 추출값} 딕셔너리. 못 찾은 값은 빈 문자열.
    """
    if not field_names:
        return {}
    if get_settings().use_dummy_extractor:
        logger.info("더미 추출기 사용 folder=%s fields=%s", folder_path, field_names)
        return _apply_field_inferences(_dummy_extract(folder_path, field_names), field_names)
    return _real_extract(folder_path, field_names, doc_hint=doc_hint)
