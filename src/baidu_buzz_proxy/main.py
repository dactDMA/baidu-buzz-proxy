from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis

from baidu_buzz_proxy import __version__
from baidu_buzz_proxy.api.routes.health import router as health_router
from baidu_buzz_proxy.api.routes.jobs import router as jobs_router
from baidu_buzz_proxy.config import get_settings
from baidu_buzz_proxy.database import Database
from baidu_buzz_proxy.services.baidu import BaiduClient
from baidu_buzz_proxy.services.buzzheavier import BuzzMultipartClient
from baidu_buzz_proxy.services.jobs import JobService
from baidu_buzz_proxy.web import admin_html, index_html, job_html
from baidu_pcs_client import BaiduPCSClient, Credentials


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.initialize()
    coordinator = Redis.from_url(settings.redis_url, decode_responses=True)
    if settings.baidu_pcs_config_path.is_file() and settings.baidu_pcs_config_path.stat().st_size:
        credentials = Credentials.from_pcs_config(settings.baidu_pcs_config_path)
    else:
        cookie_header = settings.baidu_cookies_path.read_text(encoding="utf-8").strip()
        credentials = Credentials.from_cookie_header(cookie_header)
    pcs_client = BaiduPCSClient(credentials, timeout_seconds=settings.baidu_command_timeout_seconds)
    baidu = BaiduClient(pcs_client)
    buzz = BuzzMultipartClient(
        settings.buzzheavier_base_url,
        settings.buzzheavier_access_token.get_secret_value(),
        part_size=settings.buzzheavier_part_size_mib * 1024**2,
        concurrency=settings.buzzheavier_part_concurrency,
        retries=settings.buzzheavier_part_retries,
    )
    jobs = JobService(database, settings, baidu, buzz, coordinator)
    app.state.settings = settings
    app.state.database = database
    app.state.jobs = jobs
    await jobs.start()
    try:
        yield
    finally:
        await jobs.stop()
        await coordinator.aclose()
        await database.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Baidu Buzz Proxy",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return index_html(get_settings().turnstile_site_key)

    @app.get("/jobs/{public_id}", response_class=HTMLResponse, include_in_schema=False)
    async def job_page(public_id: str) -> str:
        return job_html(public_id)

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def admin_page() -> str:
        return admin_html()

    return app


app = create_app()
