from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from baidu_buzz_proxy import __version__
from baidu_buzz_proxy.api.routes.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Baidu Buzz Proxy",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(health_router, prefix="/api")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Baidu Buzz Proxy</title>
</head>
<body>
  <main>
    <h1>Baidu Buzz Proxy</h1>
    <p>The service scaffold is running.</p>
    <p><a href="/docs">Open API documentation</a></p>
  </main>
</body>
</html>"""

    return app


app = create_app()
