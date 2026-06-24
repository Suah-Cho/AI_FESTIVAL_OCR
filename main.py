"""FastAPI 앱 진입점 (프로젝트 루트).

실행:
    uvicorn main:app --reload
    또는
    python main.py
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import STATIC_DIR
from app.routers import verification


def create_app() -> FastAPI:
    app = FastAPI(title="계약 서류 자동 검증 서비스")
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(verification.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
