from __future__ import annotations

from typing import Any, cast

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from baidu_buzz_proxy.config import Settings
from baidu_buzz_proxy.models import Job
from baidu_buzz_proxy.schemas import (
    AdminLoginRequest,
    CancelJobRequest,
    CreateJobRequest,
    SelectItemsRequest,
)
from baidu_buzz_proxy.security import (
    create_admin_jwt,
    jwt_signing_key,
    secrets_equal,
    verify_admin_jwt,
)
from baidu_buzz_proxy.services.jobs import (
    InvalidJobState,
    JobError,
    JobForbidden,
    JobNotFound,
    JobService,
)
from baidu_buzz_proxy.services.turnstile import TurnstileError, verify_turnstile

router = APIRouter()


def _services(request: Request) -> tuple[JobService, Settings]:
    return cast(JobService, request.app.state.jobs), cast(Settings, request.app.state.settings)


def _is_admin(request: Request, settings: Settings) -> bool:
    token = request.cookies.get("bbp_admin", "")
    admin_token = settings.admin_access_token.get_secret_value()
    key = jwt_signing_key(settings.admin_jwt_secret.get_secret_value(), admin_token)
    return bool(token and admin_token and verify_admin_jwt(token, key))


def _require_admin(request: Request, settings: Settings) -> None:
    if not _is_admin(request, settings):
        raise HTTPException(status_code=403, detail="Administrator session required")


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "id": job.public_id,
        "state": job.state,
        "status": job.status_message,
        "error": job.error_message,
        "output_name": job.output_name,
        "total_bytes": job.total_bytes,
        "transferred_bytes": job.transferred_bytes,
        "result_url": job.result_url,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "expires_at": job.expires_at,
        "items": [
            {
                "id": item.id,
                "path": item.relative_path,
                "name": item.name,
                "is_dir": item.is_dir,
                "size_bytes": item.size_bytes,
                "selected": item.selected,
            }
            for item in job.items
        ],
    }


def _translate_job_error(error: JobError) -> HTTPException:
    if isinstance(error, JobNotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, JobForbidden):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, InvalidJobState):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@router.post("/jobs", status_code=202)
async def create_job(payload: CreateJobRequest, request: Request) -> dict[str, Any]:
    jobs, settings = _services(request)
    try:
        await verify_turnstile(
            settings.turnstile_secret_key.get_secret_value(),
            payload.turnstile_token,
            request.client.host if request.client else None,
        )
    except (TurnstileError, httpx.HTTPError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        job, creator_key = await jobs.create_job(str(payload.share_url), payload.extraction_code)
    except JobError as error:
        raise _translate_job_error(error) from error
    return {
        "id": job.public_id,
        "creator_key": creator_key,
        "job_url": f"/jobs/{job.public_id}?key={creator_key}",
    }


@router.get("/jobs/{public_id}")
async def get_job(public_id: str, request: Request) -> dict[str, Any]:
    jobs, _ = _services(request)
    try:
        return _job_payload(await jobs.get_job(public_id))
    except JobError as error:
        raise _translate_job_error(error) from error


@router.post("/jobs/{public_id}/selection", status_code=202)
async def select_items(
    public_id: str, payload: SelectItemsRequest, request: Request
) -> dict[str, Any]:
    jobs, _ = _services(request)
    try:
        job = await jobs.select_items(
            public_id,
            payload.creator_key,
            payload.item_ids,
            payload.select_all,
            payload.output_name,
        )
        return _job_payload(job)
    except JobError as error:
        raise _translate_job_error(error) from error


@router.post("/jobs/{public_id}/cancel", status_code=202)
async def cancel_job(public_id: str, payload: CancelJobRequest, request: Request) -> dict[str, Any]:
    jobs, settings = _services(request)
    try:
        job = await jobs.cancel(
            public_id, payload.creator_key, is_admin=_is_admin(request, settings)
        )
        return _job_payload(job)
    except JobError as error:
        raise _translate_job_error(error) from error


@router.post("/admin/session", status_code=204)
async def admin_login(payload: AdminLoginRequest, request: Request) -> Response:
    _, settings = _services(request)
    configured = settings.admin_access_token.get_secret_value()
    if not configured or not secrets_equal(configured, payload.access_token):
        raise HTTPException(status_code=403, detail="Invalid administrator token")
    key = jwt_signing_key(settings.admin_jwt_secret.get_secret_value(), configured)
    response = Response(status_code=204)
    response.set_cookie(
        "bbp_admin",
        create_admin_jwt(key),
        max_age=12 * 3600,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return response


@router.delete("/admin/session", status_code=204)
async def admin_logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie("bbp_admin", path="/")
    return response


@router.get("/admin/jobs")
async def admin_jobs(request: Request) -> dict[str, Any]:
    jobs, settings = _services(request)
    _require_admin(request, settings)
    recent_jobs = await jobs.list_recent_jobs(limit=100)
    return {
        "jobs": [
            {
                "id": job.public_id,
                "state": job.state,
                "status": job.status_message,
                "error": job.error_message,
                "output_name": job.output_name,
                "total_bytes": job.total_bytes,
                "transferred_bytes": job.transferred_bytes,
                "result_url": job.result_url,
                "cancel_requested": job.cancel_requested,
                "cleanup_completed": job.cleanup_completed,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "expires_at": job.expires_at,
            }
            for job in recent_jobs
        ]
    }
