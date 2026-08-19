from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from .api import router
from .app_api import router as app_router

app = FastAPI(
    title="Gramly Welcome API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(router)
app.include_router(app_router)
app.mount("/metrics", make_asgi_app())


def run() -> None:
    uvicorn.run("gramly_welcome.main:app", host="0.0.0.0", port=8080, proxy_headers=True)


if __name__ == "__main__":
    run()
