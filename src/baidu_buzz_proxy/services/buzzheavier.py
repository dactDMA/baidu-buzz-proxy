from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx


class BuzzheavierError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UploadSession:
    upload_id: str
    location: str


ProgressCallback = Callable[[int], Awaitable[None]]
PartProgressCallback = Callable[[int], Awaitable[None]]
PrepareProgressCallback = Callable[[int], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]


class BuzzMultipartClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        part_size: int = 16 * 1024 * 1024,
        concurrency: int = 2,
        retries: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.part_size = part_size
        self.concurrency = concurrency
        self.retries = retries
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        self.client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(60, read=300, write=300),
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def create_upload(self, name: str) -> UploadSession:
        response = await self.client.post(urljoin(self.base_url, "api/upload"), json={"name": name})
        await self._raise_for_status(response, "create upload")
        payload = response.json()
        data = payload.get("data", payload)
        upload_id = str(data.get("id", ""))
        location = str(data.get("location", ""))
        if not upload_id or not location:
            raise BuzzheavierError("Buzzheavier returned an invalid upload session")
        return UploadSession(upload_id=upload_id, location=location)

    async def upload(
        self,
        name: str,
        stream: AsyncIterator[bytes],
        *,
        progress: ProgressCallback,
        prepare_progress: PrepareProgressCallback | None = None,
        is_cancelled: CancelCallback,
    ) -> str:
        session = await self.create_upload(name)
        inflight: set[asyncio.Task[tuple[int, int]]] = set()
        part_number = 1
        confirmed = 0
        part_progress: dict[int, int] = {}
        progress_lock = asyncio.Lock()
        last_report_at = 0.0

        async def report_part(number: int, sent: int) -> None:
            nonlocal last_report_at
            async with progress_lock:
                part_progress[number] = max(part_progress.get(number, 0), sent)
                now = time.monotonic()
                if now - last_report_at < 0.75:
                    return
                last_report_at = now
                await progress(confirmed + sum(part_progress.values()))

        async def confirm_part(number: int, size: int) -> None:
            nonlocal confirmed, last_report_at
            async with progress_lock:
                part_progress.pop(number, None)
                confirmed += size
                last_report_at = time.monotonic()
                await progress(confirmed + sum(part_progress.values()))

        try:
            async for part in self._parts(stream, prepare_progress=prepare_progress):
                if await is_cancelled():
                    raise asyncio.CancelledError
                while len(inflight) >= self.concurrency:
                    completed, inflight = await asyncio.wait(
                        inflight, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in completed:
                        completed_number, completed_size = task.result()
                        await confirm_part(completed_number, completed_size)
                current_number = part_number

                async def report_current(sent: int, number: int = current_number) -> None:
                    await report_part(number, sent)

                inflight.add(
                    asyncio.create_task(
                        self._upload_part(
                            session,
                            current_number,
                            part,
                            report_current,
                        )
                    )
                )
                part_number += 1

            if inflight:
                for completed_number, completed_size in await asyncio.gather(*inflight):
                    await confirm_part(completed_number, completed_size)
                inflight.clear()
        finally:
            for task in inflight:
                task.cancel()
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)

        if await is_cancelled():
            raise asyncio.CancelledError
        return await self.complete_upload(session)

    async def complete_upload(self, session: UploadSession) -> str:
        endpoint = urljoin(self.base_url, f"api/upload/{session.upload_id}")
        response = await self.client.post(endpoint)
        await self._raise_for_status(response, "complete upload")
        payload = response.json()
        data: dict[str, Any] = payload.get("data", payload)
        for key in ("url", "downloadUrl", "link"):
            if data.get(key):
                return str(data[key])
        file_id = data.get("id") or data.get("fileId") or session.upload_id
        return urljoin(self.base_url, str(file_id))

    async def _upload_part(
        self,
        session: UploadSession,
        part_number: int,
        content: bytes,
        progress: PartProgressCallback,
    ) -> tuple[int, int]:
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.patch(
                    urljoin(self.base_url, session.location),
                    headers={
                        "Upload-Length": str(len(content)),
                        "Upload-Part-Number": str(part_number),
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(content)),
                    },
                    content=self._stream_part(content, progress),
                )
                await self._raise_for_status(response, f"upload part {part_number}")
                return part_number, len(content)
            except (httpx.HTTPError, BuzzheavierError):
                if attempt >= self.retries:
                    raise
                await asyncio.sleep(min(2**attempt, 20))
        raise AssertionError("unreachable")

    @staticmethod
    async def _stream_part(
        content: bytes,
        progress: PartProgressCallback,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        sent = 0
        for offset in range(0, len(content), chunk_size):
            chunk = content[offset : offset + chunk_size]
            yield chunk
            sent += len(chunk)
            await progress(sent)

    async def _parts(
        self,
        stream: AsyncIterator[bytes],
        *,
        prepare_progress: PrepareProgressCallback | None = None,
    ) -> AsyncIterator[bytes]:
        buffer = bytearray()
        preparing_first_part = True
        async for chunk in stream:
            buffer.extend(chunk)
            if preparing_first_part and prepare_progress:
                await prepare_progress(min(len(buffer), self.part_size))
            while len(buffer) >= self.part_size:
                part = bytes(buffer[: self.part_size])
                del buffer[: self.part_size]
                preparing_first_part = False
                yield part
        if buffer:
            yield bytes(buffer)

    @staticmethod
    async def _raise_for_status(response: httpx.Response, operation: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:500]
            raise BuzzheavierError(
                f"Buzzheavier could not {operation}: HTTP {response.status_code} {detail}"
            ) from error
