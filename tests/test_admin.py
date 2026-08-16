from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from baidu_buzz_proxy.api.routes.jobs import router
from baidu_buzz_proxy.config import Settings


class FakeJobs:
    def __init__(self) -> None:
        self.limit = 0

    async def list_recent_jobs(self, limit: int) -> list[Any]:
        self.limit = limit
        now = datetime.now(UTC)
        return [
            SimpleNamespace(
                public_id="job-1",
                state="transferring",
                status_message="Uploading to Buzzheavier",
                error_message="",
                output_name="archive.zip",
                total_bytes=200,
                transferred_bytes=100,
                result_url="",
                cancel_requested=False,
                cleanup_completed=False,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(days=8),
            )
        ]


@pytest.mark.asyncio
async def test_admin_jobs_require_and_accept_admin_session() -> None:
    application = FastAPI()
    jobs = FakeJobs()
    application.state.jobs = jobs
    application.state.settings = Settings(admin_access_token="test-admin-token")
    application.include_router(router, prefix="/api")
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/admin/jobs")
        assert denied.status_code == 403

        login = await client.post("/api/admin/session", json={"access_token": "test-admin-token"})
        assert login.status_code == 204

        response = await client.get("/api/admin/jobs")
        assert response.status_code == 200
        assert response.json()["jobs"][0]["id"] == "job-1"
        assert jobs.limit == 100

        logout = await client.delete("/api/admin/session")
        assert logout.status_code == 204
        assert (await client.get("/api/admin/jobs")).status_code == 403
