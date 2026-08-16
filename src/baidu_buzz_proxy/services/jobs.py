from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from baidu_buzz_proxy.config import Settings
from baidu_buzz_proxy.database import Database
from baidu_buzz_proxy.models import TERMINAL_JOB_STATES, Job, JobItem, JobState
from baidu_buzz_proxy.security import hash_secret, new_creator_secret, verify_secret
from baidu_buzz_proxy.services.baidu import BaiduError, BaiduPCSClient
from baidu_buzz_proxy.services.buzzheavier import BuzzMultipartClient
from baidu_buzz_proxy.services.streams import SourceFile, build_zip_stream, stream_baidu_file


class JobError(RuntimeError):
    pass


class JobNotFound(JobError):
    pass


class JobForbidden(JobError):
    pass


class InvalidJobState(JobError):
    pass


_UNSAFE_OUTPUT_RE = re.compile(r"[\x00-\x1f\x7f/\\]+")


def safe_output_name(value: str, fallback: str) -> str:
    cleaned = _UNSAFE_OUTPUT_RE.sub("_", value).strip(" .")
    return (cleaned or fallback)[:200]


class JobService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        baidu: BaiduPCSClient,
        buzz: BuzzMultipartClient,
        coordinator: Redis | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.baidu = baidu
        self.buzz = buzz
        self.coordinator = coordinator
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.workers: list[asyncio.Task[None]] = []
        self.cleanup_task: asyncio.Task[None] | None = None
        self.baidu_mutation_lock = asyncio.Lock()

    async def start(self) -> None:
        interrupted_jobs = await self._mark_interrupted_jobs()
        self.workers = [
            asyncio.create_task(self._worker(), name=f"job-worker-{index}")
            for index in range(self.settings.max_active_jobs)
        ]
        self.cleanup_task = asyncio.create_task(self._expiration_loop(), name="job-expiration")
        for public_id in interrupted_jobs:
            await self.queue.put(("cleanup", public_id))

    async def stop(self) -> None:
        tasks = [*self.workers]
        if self.cleanup_task:
            tasks.append(self.cleanup_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.buzz.close()

    async def create_job(self, share_url: str, extraction_code: str) -> tuple[Job, str]:
        async with self.database.sessions() as session:
            result = await session.execute(
                select(func.count(Job.id)).where(Job.state.not_in(TERMINAL_JOB_STATES))
            )
            if (result.scalar_one() or 0) >= self.settings.max_pending_jobs:
                raise JobError("The job queue is currently full")
        public_id = str(uuid.uuid4())
        creator_secret = new_creator_secret()
        now = datetime.now(UTC)
        job = Job(
            public_id=public_id,
            creator_secret_hash=hash_secret(creator_secret),
            share_url=share_url,
            extraction_code=extraction_code,
            state=JobState.QUEUED_IMPORT,
            status_message="Waiting to import the share",
            temp_path=f"/ProxyJobs/{public_id}",
            expires_at=now + timedelta(days=self.settings.job_page_ttl_days),
        )
        async with self.database.sessions() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
        await self.queue.put(("import", public_id))
        return job, creator_secret

    async def get_job(self, public_id: str) -> Job:
        async with self.database.sessions() as session:
            result = await session.execute(
                select(Job).where(Job.public_id == public_id).options(selectinload(Job.items))
            )
            job = result.scalar_one_or_none()
            if job is None:
                raise JobNotFound("Job not found")
            return job

    async def list_recent_jobs(self, limit: int = 100) -> list[Job]:
        async with self.database.sessions() as session:
            result = await session.execute(select(Job).order_by(Job.created_at.desc()).limit(limit))
            return list(result.scalars())

    async def select_items(
        self,
        public_id: str,
        creator_key: str,
        item_ids: list[int],
        select_all: bool,
        output_name: str,
    ) -> Job:
        async with self.database.sessions() as session:
            result = await session.execute(
                select(Job).where(Job.public_id == public_id).options(selectinload(Job.items))
            )
            job = result.scalar_one_or_none()
            if job is None:
                raise JobNotFound("Job not found")
            self._require_creator(job, creator_key)
            if job.state != JobState.AWAITING_SELECTION:
                raise InvalidJobState("This job is not waiting for a selection")

            chosen_ids = set(item_ids)
            explicitly_chosen = [item for item in job.items if item.id in chosen_ids]
            if not select_all and not explicitly_chosen:
                raise JobError("Select at least one file or folder")

            chosen_directories = [item.relative_path for item in explicitly_chosen if item.is_dir]
            for item in job.items:
                item.selected = (
                    select_all
                    or item.id in chosen_ids
                    or any(
                        PurePosixPath(item.relative_path).is_relative_to(PurePosixPath(directory))
                        for directory in chosen_directories
                    )
                )

            selected_files = [item for item in job.items if item.selected and not item.is_dir]
            if not selected_files:
                raise JobError("The selection contains no files")
            single_file = len(selected_files) == 1 and not any(
                item.selected and item.is_dir for item in job.items
            )
            fallback = selected_files[0].name if single_file else f"baidu-{public_id[:8]}.zip"
            requested = safe_output_name(output_name, fallback)
            if not single_file and not requested.lower().endswith(".zip"):
                requested += ".zip"
            job.output_name = requested
            job.total_bytes = sum(item.size_bytes for item in selected_files)
            job.transferred_bytes = 0
            job.state = JobState.QUEUED_TRANSFER
            job.status_message = "Waiting for a transfer slot"
            await session.commit()

        await self.queue.put(("transfer", public_id))
        return await self.get_job(public_id)

    async def cancel(self, public_id: str, creator_key: str, is_admin: bool) -> Job:
        needs_cleanup = False
        async with self.database.sessions() as session:
            result = await session.execute(select(Job).where(Job.public_id == public_id))
            job = result.scalar_one_or_none()
            if job is None:
                raise JobNotFound("Job not found")
            if not is_admin:
                self._require_creator(job, creator_key)
            if JobState(job.state) in TERMINAL_JOB_STATES:
                return job
            needs_cleanup = job.state == JobState.AWAITING_SELECTION
            job.cancel_requested = True
            job.status_message = "Cancellation requested"
            await session.commit()
            await session.refresh(job)
        if needs_cleanup:
            await self.queue.put(("cancel", public_id))
        return job

    @staticmethod
    def _require_creator(job: Job, creator_key: str) -> None:
        if not creator_key or not verify_secret(job.creator_secret_hash, creator_key):
            raise JobForbidden("The creator key is invalid")

    async def _worker(self) -> None:
        while True:
            action, public_id = await self.queue.get()
            try:
                if action == "import":
                    await self._import_job(public_id)
                elif action == "transfer":
                    await self._transfer_job(public_id)
                elif action == "cancel":
                    await self._set_cancelled(public_id)
                    await self._cleanup_remote(public_id)
                elif action == "cleanup":
                    await self._cleanup_remote(public_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._fail_job(public_id, error)
                await self._cleanup_remote(public_id)
            finally:
                self.queue.task_done()

    async def _import_job(self, public_id: str) -> None:
        job = await self.get_job(public_id)
        if job.cancel_requested:
            await self._set_cancelled(public_id)
            return
        await self._set_state(public_id, JobState.IMPORTING, "Checking Baidu account quota")
        quota = await self.baidu.quota()
        reserve = self.settings.baidu_reserve_gib * 1024**3
        if quota.free_bytes <= reserve:
            raise JobError("Baidu free space is below the configured reserve")

        async with self._baidu_mutation_lock():
            await self._set_message(public_id, "Importing the public share into Baidu")
            await self.baidu.mkdir("/ProxyJobs")
            await self.baidu.mkdir(job.temp_path)
            try:
                await self.baidu.change_directory(job.temp_path)
                await self.baidu.import_share(job.share_url, job.extraction_code)
            finally:
                await self.baidu.change_directory("/")

        if await self._is_cancelled(public_id):
            await self._set_cancelled(public_id)
            await self._cleanup_remote(public_id)
            return

        await self._set_message(public_id, "Reading the imported file list")
        items = await self.baidu.list_tree(job.temp_path)
        if not items:
            raise JobError("The share imported successfully but contains no files")
        fs_id = await self.baidu.metadata_fs_id(job.temp_path)
        quota_after_import = await self.baidu.quota()
        if quota_after_import.free_bytes < reserve:
            raise JobError("The imported share would use the configured Baidu storage reserve")
        async with self.database.sessions() as session:
            result = await session.execute(select(Job).where(Job.public_id == public_id))
            stored_job = result.scalar_one()
            stored_job.temp_fs_id = fs_id
            stored_job.state = JobState.AWAITING_SELECTION
            stored_job.status_message = "Choose files or folders to transfer"
            session.add_all(
                JobItem(
                    job_id=stored_job.id,
                    fs_id=item.fs_id,
                    remote_path=item.remote_path,
                    relative_path=item.relative_path,
                    name=item.name,
                    is_dir=item.is_dir,
                    size_bytes=item.size_bytes,
                )
                for item in items
            )
            await session.commit()

    async def _transfer_job(self, public_id: str) -> None:
        job = await self.get_job(public_id)
        if job.cancel_requested:
            await self._set_cancelled(public_id)
            await self._cleanup_remote(public_id)
            return
        files = [item for item in job.items if item.selected and not item.is_dir]
        if not files:
            raise JobError("No source files were selected")
        await self._set_state(public_id, JobState.TRANSFERRING, "Resolving Baidu links")

        sources: list[SourceFile] = []
        for index, item in enumerate(files, start=1):
            display_name = item.name if len(item.name) <= 160 else f"{item.name[:157]}..."
            await self._set_message(
                public_id,
                f"Resolving Baidu link {index} of {len(files)}: {display_name}",
            )
            urls = await self.baidu.locate(item.remote_path)
            sources.append(
                SourceFile(
                    archive_name=item.relative_path,
                    size_bytes=item.size_bytes,
                    urls=tuple(urls),
                )
            )

        await self._set_message(public_id, "Starting the Buzzheavier upload")

        selected_directories = any(item.selected and item.is_dir for item in job.items)
        segment_size = self.settings.baidu_range_size_mib * 1024**2
        concurrency = self.settings.baidu_download_concurrency
        retries = self.settings.baidu_download_retries
        stream = (
            stream_baidu_file(
                sources[0],
                segment_size=segment_size,
                concurrency=concurrency,
                retries=retries,
            )
            if len(sources) == 1 and not selected_directories
            else build_zip_stream(
                sources,
                segment_size=segment_size,
                concurrency=concurrency,
                retries=retries,
            )
        )

        async def progress(value: int) -> None:
            async with self.database.sessions() as session:
                await session.execute(
                    update(Job)
                    .where(Job.public_id == public_id)
                    .values(transferred_bytes=value, status_message="Uploading to Buzzheavier")
                )
                await session.commit()

        async def cancelled() -> bool:
            return await self._is_cancelled(public_id)

        try:
            result_url = await self.buzz.upload(
                job.output_name,
                stream,
                progress=progress,
                is_cancelled=cancelled,
            )
        except asyncio.CancelledError:
            await self._set_cancelled(public_id)
            await self._cleanup_remote(public_id)
            return

        await self._set_state(public_id, JobState.CLEANING, "Removing the temporary Baidu copy")
        cleanup_error = await self._cleanup_remote(public_id)
        async with self.database.sessions() as session:
            result = await session.execute(select(Job).where(Job.public_id == public_id))
            stored_job = result.scalar_one()
            stored_job.result_url = result_url
            stored_job.state = JobState.COMPLETED
            stored_job.status_message = (
                "Transfer complete"
                if cleanup_error is None
                else "Transfer complete; temporary cleanup needs administrator attention"
            )
            stored_job.transferred_bytes = max(stored_job.transferred_bytes, stored_job.total_bytes)
            stored_job.expires_at = datetime.now(UTC) + timedelta(
                days=self.settings.job_page_ttl_days
            )
            await session.commit()

    async def _cleanup_remote(self, public_id: str) -> Exception | None:
        try:
            job = await self.get_job(public_id)
            if job.cleanup_completed:
                return None
            fs_id = job.temp_fs_id
            if not fs_id:
                try:
                    fs_id = await self.baidu.metadata_fs_id(job.temp_path)
                except BaiduError:
                    await self._mark_cleanup_completed(public_id)
                    return None
            async with self._baidu_mutation_lock():
                try:
                    await self.baidu.remove(job.temp_path)
                finally:
                    await self.baidu.permanently_delete(fs_id)
            await self._mark_cleanup_completed(public_id)
            return None
        except Exception as error:
            return error

    async def _set_state(self, public_id: str, state: JobState, message: str) -> None:
        async with self.database.sessions() as session:
            await session.execute(
                update(Job)
                .where(Job.public_id == public_id)
                .values(state=state, status_message=message)
            )
            await session.commit()

    @asynccontextmanager
    async def _baidu_mutation_lock(self) -> AsyncIterator[None]:
        async with self.baidu_mutation_lock:
            if self.coordinator is None:
                yield
                return
            lock = self.coordinator.lock(
                "bbp:baidu-mutation",
                timeout=self.settings.baidu_command_timeout_seconds + 600,
                blocking_timeout=self.settings.baidu_command_timeout_seconds + 600,
            )
            async with lock:
                yield

    async def _set_message(self, public_id: str, message: str) -> None:
        async with self.database.sessions() as session:
            await session.execute(
                update(Job).where(Job.public_id == public_id).values(status_message=message)
            )
            await session.commit()

    async def _mark_cleanup_completed(self, public_id: str) -> None:
        async with self.database.sessions() as session:
            await session.execute(
                update(Job).where(Job.public_id == public_id).values(cleanup_completed=True)
            )
            await session.commit()

    async def _is_cancelled(self, public_id: str) -> bool:
        async with self.database.sessions() as session:
            result = await session.execute(
                select(Job.cancel_requested).where(Job.public_id == public_id)
            )
            return bool(result.scalar_one())

    async def _set_cancelled(self, public_id: str) -> None:
        await self._set_state(public_id, JobState.CANCELLED, "Job cancelled")

    async def _fail_job(self, public_id: str, error: Exception) -> None:
        message = str(error).strip() or type(error).__name__
        async with self.database.sessions() as session:
            await session.execute(
                update(Job)
                .where(Job.public_id == public_id)
                .values(
                    state=JobState.FAILED,
                    status_message="Job failed",
                    error_message=message[:2000],
                    expires_at=datetime.now(UTC)
                    + timedelta(hours=self.settings.failed_job_ttl_hours),
                )
            )
            await session.commit()

    async def _mark_interrupted_jobs(self) -> list[str]:
        active_states = [
            JobState.QUEUED_IMPORT,
            JobState.IMPORTING,
            JobState.QUEUED_TRANSFER,
            JobState.TRANSFERRING,
            JobState.CLEANING,
        ]
        async with self.database.sessions() as session:
            result = await session.execute(
                select(Job.public_id).where(Job.state.in_(active_states))
            )
            public_ids = list(result.scalars())
            await session.execute(
                update(Job)
                .where(Job.state.in_(active_states))
                .values(
                    state=JobState.FAILED,
                    status_message="Job interrupted by a service restart",
                    error_message="Transfers do not resume after a complete worker restart.",
                )
            )
            await session.commit()
        return public_ids

    async def _expiration_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            now = datetime.now(UTC)
            async with self.database.sessions() as session:
                result = await session.execute(select(Job.public_id).where(Job.expires_at < now))
                public_ids = list(result.scalars())
            deletable_ids: list[str] = []
            for public_id in public_ids:
                if await self._cleanup_remote(public_id) is None:
                    deletable_ids.append(public_id)
            async with self.database.sessions() as session:
                await session.execute(delete(Job).where(Job.public_id.in_(deletable_ids)))
                await session.commit()
