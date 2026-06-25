"""애플리케이션 설정 중앙화.

환경변수는 이곳에서만 읽어, 서비스 코드가 os.environ 에 직접 의존하지 않도록 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 기준 경로
APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

# 모델 슬롯에서 읽는 환경변수 접미사 (슬롯 존재 여부 판단용)
_OCR_SLOT_SUFFIXES = ("TYPE", "MODEL", "NAME", "BASE_URL", "API_KEY", "URL", "SECRET")


@dataclass
class OcrModelSpec:
    """추출에 사용할 모델 슬롯 하나의 설정.

    특정 벤더에 종속되지 않도록 번호(index)로만 구분하며, ``type`` 에 따라
    어댑터(호출 방식)가 결정된다.
    """

    index: int
    type: str = "openai"  # 어댑터 종류 ("openai" = OpenAI 호환 비전 API)
    model: str | None = None  # 모델 이름 (예: gpt-4o, gemini-1.5-pro)
    base_url: str | None = None  # OpenAI 호환 엔드포인트 (None=공식 OpenAI)
    api_key: str | None = None
    url: str | None = None  # 일부 OCR 전용 엔드포인트
    secret: str | None = None  # 일부 OCR 전용 시크릿
    label: str = ""  # 로그/메시지용 표시 이름

    @property
    def name(self) -> str:
        return self.label or self.model or f"OCR_MODEL_{self.index}"


class Settings:
    """런타임 설정값."""

    def __init__(self) -> None:
        # 실행 환경: dev(외부 API 직접 호출) | prod(사내 게이트웨이 경유)
        self.app_env: str = os.environ.get("APP_ENV", "dev").strip().lower()
        # prod 전용: LLM API (슬롯 BASE_URL 없을 때 fallback, /v1/chat/completions 포함 가능)
        self.prod_api_gateway_url: str | None = os.environ.get("PROD_API_GATEWAY_URL")
        # prod LLM 호출 시 필수 커스텀 헤더 (vLLM 게이트웨이)
        self.prod_llm_code_company: str | None = os.environ.get("PROD_LLM_CODE_COMPANY")
        self.prod_llm_code_service: str | None = os.environ.get("PROD_LLM_CODE_SERVICE")
        self.prod_llm_max_retries: int = int(os.environ.get("PROD_LLM_MAX_RETRIES", "2"))
        self.prod_llm_retry_delay: float = float(os.environ.get("PROD_LLM_RETRY_DELAY", "1.5"))
        self.prod_llm_max_tokens: int = int(os.environ.get("PROD_LLM_MAX_TOKENS", "4096"))
        self.prod_llm_timeout: float = float(os.environ.get("PROD_LLM_TIMEOUT", "300"))
        self.prod_vaiv_think: bool = os.environ.get("PROD_VAIV_THINK", "0") == "1"
        # prod 이미지 전처리 (VAIV OCR)
        self.prod_image_preprocess: bool = os.environ.get("PROD_IMAGE_PREPROCESS", "1") == "1"
        self.prod_image_upscale: float = float(os.environ.get("PROD_IMAGE_UPSCALE", "1.5"))
        self.prod_image_rotate: float = float(os.environ.get("PROD_IMAGE_ROTATE", "1.5"))
        # prod 기본 어댑터 타입 (슬롯별 TYPE 미지정 시)
        self.prod_default_model_type: str = os.environ.get(
            "PROD_DEFAULT_MODEL_TYPE", "vaiv"
        ).strip().lower()
        # prod 전용: Keycloak (password grant → access_token → LLM 호출 시 Bearer)
        self.keycloak_token_url: str | None = os.environ.get("KEYCLOAK_TOKEN_URL")
        self.keycloak_url: str | None = os.environ.get("KEYCLOAK_URL")
        self.keycloak_realm: str | None = os.environ.get("KEYCLOAK_REALM")
        self.keycloak_client_id: str | None = os.environ.get("KEYCLOAK_CLIENT_ID")
        self.keycloak_client_secret: str | None = os.environ.get("KEYCLOAK_CLIENT_SECRET")
        self.keycloak_username: str | None = os.environ.get("KEYCLOAK_USERNAME")
        self.keycloak_password: str | None = os.environ.get("KEYCLOAK_PASSWORD")
        self.keycloak_grant_type: str = os.environ.get("KEYCLOAK_GRANT_TYPE", "password")

        self.openai_api_key: str | None = os.environ.get("OPENAI_API_KEY")
        self.openai_vision_model: str = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
        self.use_dummy_extractor: bool = os.environ.get("USE_DUMMY_EXTRACTOR") == "1"
        self.openai_timeout: float = float(os.environ.get("OPENAI_TIMEOUT", "60"))
        # 한 폴더에서 모델에 보낼 최대 이미지 수 (비용/토큰 보호)
        self.max_images_per_folder: int = int(os.environ.get("MAX_IMAGES_PER_FOLDER", "8"))
        # 행 검증을 동시에 처리할 최대 개수 (병렬 LLM 호출 수)
        self.max_parallel_rows: int = int(os.environ.get("MAX_PARALLEL_ROWS", "5"))
        # 폴더 안 서류·필드를 종류별로 나눠 LLM 호출 (0 이면 기존 일괄 추출)
        self.split_doc_extraction: bool = os.environ.get("USE_SPLIT_DOC_EXTRACTION", "1") == "1"

        # --- 멀티 OCR(앙상블) 설정 ---
        # 모델 슬롯은 OCR_MODEL_1, OCR_MODEL_2 ... 로 순서대로 정의한다.
        # 슬롯 순서가 곧 우선순위이며, 다수결이 동점일 때 앞 슬롯이 우선 채택된다.
        # 각 슬롯 환경변수(없으면 합리적 기본값으로 보완):
        #   OCR_MODEL_{N}_TYPE      어댑터 종류 (기본 "openai")
        #   [openai] _MODEL / _BASE_URL / _API_KEY
        #   [그 외 ] _URL / _SECRET (전용 OCR 어댑터용)
        # 슬롯을 하나도 정의하지 않으면 OPENAI_* 값으로 기본 슬롯 1개를 만든다.
        self.ocr_models: list[OcrModelSpec] = self._load_ocr_models()
        # 각 모델을 몇 번 반복 호출할지(셀프 컨시스턴시). 1이면 단발 호출.
        self.ocr_samples_per_model: int = max(
            1, int(os.environ.get("OCR_SAMPLES_PER_MODEL", "1"))
        )
        # 반복 호출 시 사용할 temperature (다양성 확보). 0이면 매번 동일 경향.
        self.ocr_sample_temperature: float = float(
            os.environ.get("OCR_SAMPLE_TEMPERATURE", "0.2")
        )

        # 기본 서류 위치 지정 열 이름
        self.default_folder_col: str = os.environ.get("DEFAULT_FOLDER_COL", "파일폴더")
        self.default_file_no_col: str = os.environ.get("DEFAULT_FILE_NO_COL", "파일번호")

    @property
    def is_dev(self) -> bool:
        return self.app_env != "prod"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    def _default_model_type(self) -> str:
        return self.prod_default_model_type if self.is_prod else "openai"

    def _default_model_base_url(self) -> str | None:
        return self.prod_api_gateway_url if self.is_prod else None

    def _has_numbered_model_slots(self) -> bool:
        for i in range(1, 100):
            prefix = f"OCR_MODEL_{i}"
            if any(os.environ.get(f"{prefix}_{suffix}") for suffix in _OCR_SLOT_SUFFIXES):
                return True
        return False

    def _load_ocr_models(self) -> list[OcrModelSpec]:
        """OCR_MODEL_1, OCR_MODEL_2 ... 또는 OCR_MODEL_NAMES 로 슬롯 목록을 만든다."""
        # 운영/개발 공통: MODEL 이름만 쉼표로 나열 (URL/인증은 공통, 다수결 병합)
        names_csv = os.environ.get("OCR_MODEL_NAMES", "").strip()
        if names_csv and not self._has_numbered_model_slots():
            names = [n.strip() for n in names_csv.split(",") if n.strip()]
            slot_type = os.environ.get("OCR_MODEL_TYPE") or self._default_model_type()
            base_url = os.environ.get("OCR_MODEL_BASE_URL") or self._default_model_base_url()
            shared_api_key = os.environ.get("OCR_MODEL_API_KEY") or self.openai_api_key
            return [
                OcrModelSpec(
                    index=i + 1,
                    type=slot_type,
                    model=name,
                    base_url=base_url,
                    api_key=shared_api_key,
                    label=name,
                )
                for i, name in enumerate(names)
            ]

        specs: list[OcrModelSpec] = []
        i = 1
        while True:
            prefix = f"OCR_MODEL_{i}"
            if not any(
                os.environ.get(f"{prefix}_{suffix}") for suffix in _OCR_SLOT_SUFFIXES
            ):
                break
            model = (
                os.environ.get(f"{prefix}_MODEL")
                or os.environ.get(f"{prefix}_NAME")
                or (None if self.is_prod else self.openai_vision_model)
            )
            if not model:
                break
            specs.append(
                OcrModelSpec(
                    index=i,
                    type=(
                        os.environ.get(f"{prefix}_TYPE") or self._default_model_type()
                    ).strip().lower(),
                    model=model,
                    base_url=os.environ.get(f"{prefix}_BASE_URL")
                    or self._default_model_base_url(),
                    api_key=os.environ.get(f"{prefix}_API_KEY") or self.openai_api_key,
                    url=os.environ.get(f"{prefix}_URL"),
                    secret=os.environ.get(f"{prefix}_SECRET"),
                    label=os.environ.get(f"{prefix}_NAME") or model,
                )
            )
            i += 1

        if not specs and not self.is_prod:
            specs.append(
                OcrModelSpec(
                    index=1,
                    type="openai",
                    model=self.openai_vision_model,
                    api_key=self.openai_api_key,
                    label=self.openai_vision_model,
                )
            )
        return specs


@lru_cache
def get_settings() -> Settings:
    return Settings()
