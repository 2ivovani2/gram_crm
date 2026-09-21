from __future__ import annotations

import logging
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from .admin_api import router as admin_router
from .admin_catalog import router as admin_catalog_router
from .api import router
from .app_api import router as app_router
from .catalog_media import router as catalog_media_router
from .editor_api import router as editor_router
from .editor_assets import admin_router as emoji_admin_router
from .editor_assets import router as editor_assets_router

app = FastAPI(
    title="Gramly Welcome API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(router)
app.include_router(app_router)
app.include_router(admin_router)
app.include_router(admin_catalog_router)
app.include_router(catalog_media_router)
app.include_router(editor_router)
app.include_router(editor_assets_router)
app.include_router(emoji_admin_router)
app.mount("/metrics", make_asgi_app())

logger = logging.getLogger(__name__)


@app.middleware("http")
async def request_correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if 1 <= len(supplied) <= 64 and supplied.isascii() else uuid.uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "Не удалось выполнить запрос. Повторите попытку.",
                "request_id": request_id,
            },
        )
    response.headers["X-Request-ID"] = request_id
    return response


def run() -> None:
    uvicorn.run("gramly_welcome.main:app", host="0.0.0.0", port=8080, proxy_headers=True)


if __name__ == "__main__":
    run()
