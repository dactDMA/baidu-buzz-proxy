from __future__ import annotations

import asyncio
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass

import httpx
import zipstream  # type: ignore[import-untyped]

from baidu_buzz_proxy.services.baidu import BAIDU_DOWNLOAD_USER_AGENT


class SourceDownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceFile:
    archive_name: str
    size_bytes: int
    urls: tuple[str, ...]


async def stream_baidu_file(
    source: SourceFile,
    *,
    chunk_size: int = 1024 * 1024,
    retries: int = 5,
) -> AsyncIterator[bytes]:
    offset = 0
    attempt = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": BAIDU_DOWNLOAD_USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(60, read=120),
    ) as client:
        while offset < source.size_bytes:
            url = source.urls[attempt % len(source.urls)]
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    if offset and response.status_code != 206:
                        raise SourceDownloadError("Baidu did not honor a resumed range request")
                    async for chunk in response.aiter_bytes(chunk_size):
                        if not chunk:
                            continue
                        offset += len(chunk)
                        yield chunk
                if offset < source.size_bytes:
                    raise SourceDownloadError("Baidu closed the source stream early")
            except (httpx.HTTPError, SourceDownloadError) as error:
                attempt += 1
                if attempt > retries:
                    raise SourceDownloadError(
                        f"Source download failed after {retries} retries"
                    ) from error
                await asyncio.sleep(min(2**attempt, 20))


def _sync_file_chunks(source: SourceFile, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    offset = 0
    attempt = 0
    with httpx.Client(
        headers={"User-Agent": BAIDU_DOWNLOAD_USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(60, read=120),
    ) as client:
        while offset < source.size_bytes:
            url = source.urls[attempt % len(source.urls)]
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    if offset and response.status_code != 206:
                        raise SourceDownloadError("Baidu did not honor a resumed range request")
                    for chunk in response.iter_bytes(chunk_size):
                        if chunk:
                            offset += len(chunk)
                            yield chunk
                if offset < source.size_bytes:
                    raise SourceDownloadError("Baidu closed the source stream early")
            except (httpx.HTTPError, SourceDownloadError) as error:
                attempt += 1
                if attempt > 5:
                    raise SourceDownloadError("Source download retry limit reached") from error


_END = object()


def _next_or_end(iterator: Iterator[bytes]) -> bytes | object:
    try:
        return next(iterator)
    except StopIteration:
        return _END


async def async_from_sync(iterator: Iterator[bytes]) -> AsyncIterator[bytes]:
    while True:
        value = await asyncio.to_thread(_next_or_end, iterator)
        if value is _END:
            return
        yield value  # type: ignore[misc]


def build_zip_stream(sources: list[SourceFile]) -> AsyncIterator[bytes]:
    archive = zipstream.ZipStream(compress_type=zipfile.ZIP_STORED, sized=False)
    for source in sources:
        archive.add(
            _sync_file_chunks(source),
            source.archive_name,
            size=source.size_bytes,
        )
    return async_from_sync(iter(archive))


async def track_progress(
    stream: AsyncIterator[bytes], callback: Callable[[int], None]
) -> AsyncIterator[bytes]:
    transferred = 0
    async for chunk in stream:
        transferred += len(chunk)
        callback(transferred)
        yield chunk
