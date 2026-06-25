"""환경(dev/prod)에 따라 외부 API 호출 방식을 분기한다.

- dev  : OCR_MODEL 슬롯 설정값으로 외부 API를 직접 호출 (OpenAI SDK / requests)
- prod : Keycloak password grant 로 access_token 발급 후,
         LLM 호출 시 Bearer + code_company / code_service 헤더 전달
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.core.config import OcrModelSpec, get_settings
from app.services.keycloak_auth import get_access_token

logger = logging.getLogger(__name__)


def _parse_json_response(raw: str) -> dict:
    """LLM 응답 문자열에서 JSON 객체를 추출한다 (vLLM은 ```json 래핑 가능)."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 응답에서 JSON 객체를 찾을 수 없습니다.")
    return json.loads(text[start : end + 1])


def _prod_llm_headers() -> dict[str, str]:
    """prod LLM 호출 헤더: Bearer + code_company / code_service."""
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
    }
    if settings.prod_llm_code_company:
        headers["code_company"] = settings.prod_llm_code_company
    if settings.prod_llm_code_service:
        headers["code_service"] = settings.prod_llm_code_service
    return headers


def _vaiv_chat_url(spec: OcrModelSpec) -> str:
    """VAIV /api/chat URL (전체 URL 또는 베이스 URL 모두 허용)."""
    settings = get_settings()
    base = (spec.base_url or settings.prod_api_gateway_url or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "prod 환경에서는 OCR_MODEL_BASE_URL / PROD_API_GATEWAY_URL 이 필요합니다."
        )
    if base.endswith("/api/chat"):
        return base
    return f"{base}/api/chat"


def vaiv_chat_json(
    spec: OcrModelSpec,
    *,
    system_prompt: str,
    user_prompt: str,
    images_b64: list[str],
    temperature: float,
) -> dict:
    """prod: VAIV /api/chat API 호출 (Keycloak + code_company/code_service).

    응답 ``message.content`` 에서 JSON 필드를 파싱해 반환한다.
    """
    import requests

    settings = get_settings()
    if not settings.is_prod:
        raise RuntimeError("vaiv 어댑터는 prod 환경에서만 사용할 수 있습니다.")

    url = _vaiv_chat_url(spec)
    headers = _prod_llm_headers()
    payload = {
        "model": spec.model,
        "stream": False,
        "temperature": temperature,
        "max_tokens": settings.prod_llm_max_tokens,
        "think": settings.prod_vaiv_think,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt, "images": images_b64},
        ],
    }

    logger.info(
        "VAIV API 호출 → model=%s url=%s images=%d temp=%.2f",
        spec.model,
        url,
        len(images_b64),
        temperature,
    )

    last_error: Exception | None = None
    connect_timeout = min(10.0, settings.prod_llm_timeout)
    for attempt in range(settings.prod_llm_max_retries + 1):
        started = time.perf_counter()
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=(connect_timeout, settings.prod_llm_timeout),
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            if resp.status_code != 200:
                raise RuntimeError(resp.text)
            data = resp.json()
            raw = (data.get("message") or {}).get("content", "")
            if not raw:
                raise RuntimeError(f"VAIV 응답에 message.content 가 없습니다: {data}")
            parsed = _parse_json_response(raw)
            logger.info(
                "VAIV API 응답 ← model=%s status=%d %.0fms fields=%s",
                spec.model,
                resp.status_code,
                elapsed_ms,
                parsed,
            )
            return parsed
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            last_error = exc
            if attempt < settings.prod_llm_max_retries:
                logger.warning(
                    "VAIV API 재시도 model=%s attempt=%d/%d %.0fms error=%s",
                    spec.model,
                    attempt + 1,
                    settings.prod_llm_max_retries,
                    elapsed_ms,
                    exc,
                )
                time.sleep(settings.prod_llm_retry_delay)
                continue
            logger.error(
                "VAIV API 실패 model=%s %.0fms error=%s",
                spec.model,
                elapsed_ms,
                exc,
            )
            break

    raise RuntimeError(f"VAIV LLM 호출 실패 ({spec.name}): {last_error}") from last_error


def _prod_chat_url(spec: OcrModelSpec) -> str:
    """prod LLM chat/completions URL (전체 URL 또는 베이스 URL 모두 허용)."""
    settings = get_settings()
    base = (spec.base_url or settings.prod_api_gateway_url or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "prod 환경에서는 OCR_MODEL_{N}_BASE_URL 또는 PROD_API_GATEWAY_URL 이 필요합니다."
        )
    if base.endswith("/v1/chat/completions"):
        return base
    return f"{base}/v1/chat/completions"


def _direct_chat_json(spec: OcrModelSpec, content: list[dict], temperature: float) -> dict:
    """dev: OpenAI 호환 API를 SDK로 직접 호출한다."""
    from openai import OpenAI

    if not spec.api_key:
        raise RuntimeError(f"OCR_MODEL_{spec.index}({spec.name}): API 키가 없습니다.")

    logger.info(
        "OpenAI API 호출 → model=%s type=%s temp=%.2f",
        spec.model,
        spec.type,
        temperature,
    )
    started = time.perf_counter()
    client_kwargs: dict = {"api_key": spec.api_key, "timeout": get_settings().openai_timeout}
    if spec.base_url:
        client_kwargs["base_url"] = spec.base_url

    client = OpenAI(**client_kwargs)
    resp = client.chat.completions.create(
        model=spec.model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw)
    logger.info(
        "OpenAI API 응답 ← model=%s %.0fms fields=%s",
        spec.model,
        (time.perf_counter() - started) * 1000,
        parsed,
    )
    return parsed


def _prod_chat_json(spec: OcrModelSpec, content: list[dict], temperature: float) -> dict:
    """prod: Keycloak 토큰 + 커스텀 헤더로 vLLM API를 호출한다."""
    import requests

    settings = get_settings()
    url = _prod_chat_url(spec)
    headers = _prod_llm_headers()
    payload = {
        "model": spec.model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_tokens": settings.prod_llm_max_tokens,
    }

    logger.info(
        "vLLM API 호출 → model=%s url=%s temp=%.2f",
        spec.model,
        url,
        temperature,
    )

    last_error: Exception | None = None
    for attempt in range(settings.prod_llm_max_retries + 1):
        started = time.perf_counter()
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=settings.prod_llm_timeout
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            if resp.status_code != 200:
                raise RuntimeError(resp.text)
            data = resp.json()
            raw = data["choices"][0]["message"]["content"] or "{}"
            parsed = _parse_json_response(raw)
            logger.info(
                "vLLM API 응답 ← model=%s status=%d %.0fms fields=%s",
                spec.model,
                resp.status_code,
                elapsed_ms,
                parsed,
            )
            return parsed
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            last_error = exc
            if attempt < settings.prod_llm_max_retries:
                logger.warning(
                    "vLLM API 재시도 model=%s attempt=%d error=%s",
                    spec.model,
                    attempt + 1,
                    exc,
                )
                time.sleep(settings.prod_llm_retry_delay)
                continue
            logger.error("vLLM API 실패 model=%s %.0fms error=%s", spec.model, elapsed_ms, exc)
            break

    raise RuntimeError(f"LLM 호출 실패 ({spec.name}): {last_error}") from last_error


def chat_completion_json(spec: OcrModelSpec, content: list[dict], temperature: float) -> dict:
    """환경에 맞는 방식으로 비전 LLM을 호출해 JSON 객체를 반환한다."""
    settings = get_settings()
    if settings.is_prod:
        return _prod_chat_json(spec, content, temperature)
    return _direct_chat_json(spec, content, temperature)


def _direct_ocr_post(
    url: str, secret: str, files: dict, timeout: float
) -> dict[str, Any]:
    """dev: 전용 OCR 엔드포인트에 직접 multipart POST."""
    import requests

    resp = requests.post(
        url,
        headers={"X-OCR-SECRET": secret},
        files=files,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _prod_ocr_post(files: dict, timeout: float) -> dict[str, Any]:
    """prod: Keycloak 토큰 + 커스텀 헤더로 사내 OCR 프록시에 multipart POST."""
    import requests

    settings = get_settings()
    if not settings.prod_api_gateway_url:
        raise RuntimeError("prod 환경에서는 PROD_API_GATEWAY_URL 이 필요합니다.")

    base = settings.prod_api_gateway_url.rstrip("/")
    url = base if base.endswith("/v1/ocr") else f"{base}/v1/ocr"
    headers = {k: v for k, v in _prod_llm_headers().items() if k != "Content-Type"}
    resp = requests.post(url, headers=headers, files=files, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def ocr_post(
    *,
    url: str | None,
    secret: str | None,
    files: dict,
    timeout: float,
) -> dict[str, Any]:
    """환경에 맞는 방식으로 OCR 원문 추출 API를 호출한다."""
    settings = get_settings()
    if settings.is_prod:
        return _prod_ocr_post(files, timeout)
    if not url or not secret:
        raise RuntimeError("OCR _URL / _SECRET 이 설정되지 않았습니다.")
    return _direct_ocr_post(url, secret, files, timeout)
