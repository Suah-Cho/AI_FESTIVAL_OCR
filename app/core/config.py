"""애플리케이션 설정 중앙화.

환경변수는 이곳에서만 읽어, 서비스 코드가 os.environ 에 직접 의존하지 않도록 한다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 기준 경로
APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


class Settings:
    """런타임 설정값."""

    def __init__(self) -> None:
        self.openai_api_key: str | None = os.environ.get("OPENAI_API_KEY")
        self.openai_vision_model: str = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
        self.use_dummy_extractor: bool = os.environ.get("USE_DUMMY_EXTRACTOR") == "1"
        self.openai_timeout: float = float(os.environ.get("OPENAI_TIMEOUT", "60"))
        # 한 폴더에서 모델에 보낼 최대 이미지 수 (비용/토큰 보호)
        self.max_images_per_folder: int = int(os.environ.get("MAX_IMAGES_PER_FOLDER", "8"))
        # 행 검증을 동시에 처리할 최대 개수 (병렬 LLM 호출 수)
        self.max_parallel_rows: int = int(os.environ.get("MAX_PARALLEL_ROWS", "5"))
        # 기본 서류 위치 지정 열 이름
        self.default_folder_col: str = os.environ.get("DEFAULT_FOLDER_COL", "파일폴더")
        self.default_file_no_col: str = os.environ.get("DEFAULT_FILE_NO_COL", "파일번호")


@lru_cache
def get_settings() -> Settings:
    return Settings()
