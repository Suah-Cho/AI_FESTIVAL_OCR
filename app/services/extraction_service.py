"""서류 이미지에서 필드 값을 추출하는 서비스.

원칙: "값 추출은 GPT가" 담당한다. (비교/판정은 normalization.py 가 담당)

핵심 함수는 ``extract_fields_from_documents(folder_path, field_names) -> dict`` 로,
나중에 모델/제공자 교체가 쉽도록 분리되어 있다.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

# GPT에 보낼 이미지 확장자
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


def _build_prompt(field_names: list[str]) -> str:
    fields_str = ", ".join(field_names)
    keys_example = ", ".join(f'"{n}": "..."' for n in field_names)
    return (
        "너는 한국 계약/거래처 서류(사업자등록증, 신분증 등)에서 정보를 추출하는 OCR 도우미야.\n"
        f"첨부된 이미지들에서 다음 항목의 값을 찾아줘: {fields_str}\n\n"
        "규칙:\n"
        "- 반드시 JSON 객체 하나만 출력해.\n"
        f"- JSON 키는 정확히 다음과 같아야 해: {{{keys_example}}}\n"
        '- 값을 찾을 수 없으면 빈 문자열("")로 둬.\n'
        "- 추측하지 말고 서류에 적힌 값을 그대로 적어줘.\n"
    )


def _dummy_extract(folder_path: str, field_names: list[str]) -> dict:
    """GPT 호출 없이 동작 흐름을 확인하기 위한 더미 추출기.

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


def _real_extract(folder_path: str, field_names: list[str]) -> dict:
    """OpenAI 비전 모델로 폴더 안 서류에서 값을 추출한다."""
    from openai import OpenAI  # 지연 임포트: 더미 모드에서는 불필요

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout)

    files = _list_document_files(folder_path)
    image_files = [f for f in files if f.suffix.lower() in _IMAGE_EXTS][
        : settings.max_images_per_folder
    ]
    if not image_files:
        return {name: "" for name in field_names}

    content: list[dict] = [{"type": "text", "text": _build_prompt(field_names)}]
    for img in image_files:
        encoded = _encode_image(img)
        if encoded:
            content.append(encoded)

    try:
        resp = client.chat.completions.create(
            model=settings.openai_vision_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {name: "" for name in field_names}
    except Exception as exc:  # API 실패/타임아웃 등
        raise RuntimeError(f"GPT 추출 실패 ({folder_path}): {exc}") from exc

    return {name: str(data.get(name, "") or "") for name in field_names}


def extract_fields_from_documents(folder_path: str, field_names: list[str]) -> dict:
    """폴더 안 서류에서 지정한 필드 값을 추출한다.

    Args:
        folder_path: ``{파일폴더}/{파일번호}`` 경로.
        field_names: 추출할 필드 이름 목록 (사용자 입력 = 검증할 열 이름).

    Returns:
        {필드이름: 추출값} 딕셔너리. 못 찾은 값은 빈 문자열.
    """
    if not field_names:
        return {}
    if get_settings().use_dummy_extractor:
        return _dummy_extract(folder_path, field_names)
    return _real_extract(folder_path, field_names)
