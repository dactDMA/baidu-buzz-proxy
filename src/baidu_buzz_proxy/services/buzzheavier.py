from __future__ import annotations

import asyncio
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
CancelCallback = Callable[[], Awaitable[bool]]


class BuzzMultipartClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        part_size: int = 100 * 1024 * 1024,
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
        is_cancelled: CancelCallback,
    ) -> str:
        session = await self.create_upload(name)
        inflight: set[asyncio.Task[int]] = set()
        part_number = 1
        uploaded = 0

        try:
            async for part in self._parts(stream):
                if await is_cancelled():
                    raise asyncio.CancelledError
                while len(inflight) >= self.concurrency:
                    completed, inflight = await asyncio.wait(
                        inflight, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in completed:
                        uploaded += task.result()
                        await progress(uploaded)
                inflight.add(asyncio.create_task(self._upload_part(session, part_number, part)))
                part_number += 1

            if inflight:
                for size in await asyncio.gather(*inflight):
                    uploaded += size
                    await progress(uploaded)
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

    async def _upload_part(self, session: UploadSession, part_number: int, content: bytes) -> int:
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.patch(
                    urljoin(self.base_url, session.location),
                    headers={
                        "Upload-Length": str(len(content)),
                        "Upload-Part-Number": str(part_number),
                        "Content-Type": "application/octet-stream",
                    },
                    content=content,
                )
                await self._raise_for_status(response, f"upload part {part_number}")
                return len(content)
            except (httpx.HTTPError, BuzzheavierError):
                if attempt >= self.retries:
                    raise
                await asyncio.sleep(min(2**attempt, 20))
        raise AssertionError("unreachable")

    async def _parts(self, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        buffer = bytearray()
        async for chunk in stream:
            buffer.extend(chunk)
            while len(buffer) >= self.part_size:
                yield bytes(buffer[: self.part_size])
                del buffer[: self.part_size]
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
