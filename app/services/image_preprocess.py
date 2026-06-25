"""prod OCR용 이미지 전처리 (업스케일 + 미세 회전).

VAIV /api/chat 호출 전에 OpenCV로 전처리해 인식률을 높인다.
``PROD_IMAGE_PREPROCESS=0`` 이면 원본 바이트를 그대로 사용한다.
"""

from __future__ import annotations

from pathlib import Path


def read_image_jpeg_bytes(path: Path, *, preprocess: bool) -> bytes:
    """이미지 파일을 JPEG 바이트로 반환한다. preprocess=True 이면 전처리 후 인코딩."""
    if not preprocess:
        return path.read_bytes()

    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return path.read_bytes()

    from app.core.config import get_settings

    settings = get_settings()
    img = _upscale(img, settings.prod_image_upscale)
    img = _rotate(img, settings.prod_image_rotate)

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        return path.read_bytes()
    return buf.tobytes()


def _upscale(img, scale: float):
    import cv2

    if scale == 1.0:
        return img
    h, w = img.shape[:2]
    return cv2.resize(
        img,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_LANCZOS4,
    )


def _rotate(img, angle: float):
    import cv2

    if angle == 0.0:
        return img
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        img,
        m,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
