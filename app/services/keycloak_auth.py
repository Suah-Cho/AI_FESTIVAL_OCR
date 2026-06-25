"""Keycloak 인증: prod 환경에서 access_token 발급 및 캐싱.

password grant (또는 client_credentials) 로 토큰을 받아 LLM 호출 시
``Authorization: Bearer {access_token}`` 헤더에 실어 보낸다.
토큰은 만료 전까지 메모리에 캐시해 매 호출마다 Keycloak에 요청하지 않는다.
"""

from __future__ import annotations

import threading
import time
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[str, float | str | None] = {"token": None, "expires_at": 0.0}


def _resolve_token_url() -> str:
    settings = get_settings()
    if settings.keycloak_token_url:
        return settings.keycloak_token_url.rstrip("/")
    if settings.keycloak_url and settings.keycloak_realm:
        base = settings.keycloak_url.rstrip("/")
        return f"{base}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"
    raise RuntimeError(
        "Keycloak 설정이 없습니다. KEYCLOAK_TOKEN_URL 또는 "
        "KEYCLOAK_URL + KEYCLOAK_REALM 을 설정해 주세요."
    )


def _build_token_request_data() -> dict[str, str]:
    settings = get_settings()
    if not settings.keycloak_client_id or not settings.keycloak_client_secret:
        raise RuntimeError(
            "Keycloak 설정이 없습니다. KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET 을 설정해 주세요."
        )

    data = {
        "grant_type": settings.keycloak_grant_type,
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    if settings.keycloak_grant_type == "password":
        if not settings.keycloak_username or not settings.keycloak_password:
            raise RuntimeError(
                "password grant 는 KEYCLOAK_USERNAME / KEYCLOAK_PASSWORD 가 필요합니다."
            )
        data["username"] = settings.keycloak_username
        data["password"] = settings.keycloak_password
    return data


def get_access_token() -> str:
    """Keycloak access_token 을 반환한다. 유효한 캐시가 있으면 재사용한다."""
    settings = get_settings()
    now = time.time()
    with _lock:
        cached = _cache["token"]
        expires_at = float(_cache["expires_at"] or 0)
        if cached and now < expires_at - 30:  # 만료 30초 전까지 재사용
            logger.debug("Keycloak 토큰 캐시 재사용 (%.0fs 후 만료)", expires_at - now)
            return str(cached)

    logger.info("Keycloak 토큰 발급 요청 (grant_type=%s)", settings.keycloak_grant_type)
    import requests

    resp = requests.post(
        _resolve_token_url(),
        data=_build_token_request_data(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=settings.openai_timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Keycloak 토큰 발급 실패: {resp.text}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Keycloak 응답에 access_token 이 없습니다.")

    expires_in = int(data.get("expires_in", 300))
    with _lock:
        _cache["token"] = token
        _cache["expires_at"] = time.time() + expires_in

    logger.info("Keycloak 토큰 발급 성공 (expires_in=%ss)", expires_in)
    return str(token)
