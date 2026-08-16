from __future__ import annotations

import asyncio
import re
import time
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
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


_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.I)


def _segment_count(size: int, segment_size: int) -> int:
    return (size + segment_size - 1) // segment_size


def _segment_bounds(index: int, size: int, segment_size: int) -> tuple[int, int]:
    start = index * segment_size
    return start, min(start + segment_size, size) - 1


def _validate_range_response(
    response: httpx.Response, start: int, end: int, total_size: int
) -> None:
    response.raise_for_status()
    if response.status_code == 200 and start == 0 and end == total_size - 1:
        return
    if response.status_code != 206:
        raise SourceDownloadError("Baidu did not honor a parallel range request")
    match = _CONTENT_RANGE_RE.match(response.headers.get("Content-Range", ""))
    if not match or tuple(map(int, match.groups())) != (start, end, total_size):
        raise SourceDownloadError("Baidu returned an invalid Content-Range")


async def _download_async_segment(
    client: httpx.AsyncClient,
    source: SourceFile,
    index: int,
    segment_size: int,
    retries: int,
) -> bytes:
    start, end = _segment_bounds(index, source.size_bytes, segment_size)
    expected_size = end - start + 1
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        url = source.urls[attempt % len(source.urls)]
        try:
            async with client.stream(
                "GET", url, headers={"Range": f"bytes={start}-{end}"}
            ) as response:
                _validate_range_response(response, start, end, source.size_bytes)
                data = bytearray()
                if response.is_stream_consumed:
                    data.extend(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        data.extend(chunk)
                        if len(data) > expected_size:
                            raise SourceDownloadError("Baidu returned too much range data")
            if len(data) != expected_size:
                raise SourceDownloadError("Baidu returned an incomplete range")
            return bytes(data)
        except (httpx.HTTPError, SourceDownloadError) as error:
            last_error = error
            if attempt < retries:
                await asyncio.sleep(min(2**attempt, 10))
    raise SourceDownloadError(
        f"Baidu range {start}-{end} failed after {retries + 1} attempts"
    ) from last_error


async def stream_baidu_file(
    source: SourceFile,
    *,
    chunk_size: int = 1024 * 1024,
    segment_size: int = 16 * 1024 * 1024,
    concurrency: int = 10,
    retries: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[bytes]:
    if source.size_bytes <= 0:
        return
    if not source.urls:
        raise SourceDownloadError("Baidu returned no source URLs")
    segment_size = max(segment_size, chunk_size)
    count = _segment_count(source.size_bytes, segment_size)
    concurrency = max(1, min(concurrency, count))
    tasks: dict[int, asyncio.Task[bytes]] = {}
    next_submit = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": BAIDU_DOWNLOAD_USER_AGENT, "Accept-Encoding": "identity"},
        follow_redirects=True,
        timeout=httpx.Timeout(60, read=120),
        limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
        transport=transport,
    ) as client:
        try:
            while next_submit < concurrency:
                tasks[next_submit] = asyncio.create_task(
                    _download_async_segment(client, source, next_submit, segment_size, retries)
                )
                next_submit += 1
            for next_yield in range(count):
                data = await tasks.pop(next_yield)
                if next_submit < count:
                    tasks[next_submit] = asyncio.create_task(
                        _download_async_segment(client, source, next_submit, segment_size, retries)
                    )
                    next_submit += 1
                for offset in range(0, len(data), chunk_size):
                    yield data[offset : offset + chunk_size]
        finally:
            for task in tasks.values():
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)


def _download_sync_segment(
    client: httpx.Client,
    source: SourceFile,
    index: int,
    segment_size: int,
    retries: int,
) -> bytes:
    start, end = _segment_bounds(index, source.size_bytes, segment_size)
    expected_size = end - start + 1
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        url = source.urls[attempt % len(source.urls)]
        try:
            with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
                _validate_range_response(response, start, end, source.size_bytes)
                data = bytearray()
                if response.is_stream_consumed:
                    data.extend(response.content)
                else:
                    for chunk in response.iter_raw():
                        data.extend(chunk)
                        if len(data) > expected_size:
                            raise SourceDownloadError("Baidu returned too much range data")
            if len(data) != expected_size:
                raise SourceDownloadError("Baidu returned an incomplete range")
            return bytes(data)
        except (httpx.HTTPError, SourceDownloadError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
    raise SourceDownloadError(
        f"Baidu range {start}-{end} failed after {retries + 1} attempts"
    ) from last_error


def _sync_file_chunks(
    source: SourceFile,
    *,
    chunk_size: int = 1024 * 1024,
    segment_size: int = 16 * 1024 * 1024,
    concurrency: int = 10,
    retries: int = 5,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[bytes]:
    if source.size_bytes <= 0:
        return
    if not source.urls:
        raise SourceDownloadError("Baidu returned no source URLs")
    segment_size = max(segment_size, chunk_size)
    count = _segment_count(source.size_bytes, segment_size)
    concurrency = max(1, min(concurrency, count))
    futures: dict[int, Future[bytes]] = {}
    next_submit = 0
    with (
        httpx.Client(
            headers={"User-Agent": BAIDU_DOWNLOAD_USER_AGENT, "Accept-Encoding": "identity"},
            follow_redirects=True,
            timeout=httpx.Timeout(60, read=120),
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
            transport=transport,
        ) as client,
        ThreadPoolExecutor(max_workers=concurrency) as executor,
    ):
        while next_submit < concurrency:
            futures[next_submit] = executor.submit(
                _download_sync_segment,
                client,
                source,
                next_submit,
                segment_size,
                retries,
            )
            next_submit += 1
        try:
            for next_yield in range(count):
                data = futures.pop(next_yield).result()
                if next_submit < count:
                    futures[next_submit] = executor.submit(
                        _download_sync_segment,
                        client,
                        source,
                        next_submit,
                        segment_size,
                        retries,
                    )
                    next_submit += 1
                for offset in range(0, len(data), chunk_size):
                    yield data[offset : offset + chunk_size]
        finally:
            for future in futures.values():
                future.cancel()


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


def build_zip_stream(
    sources: list[SourceFile],
    *,
    segment_size: int = 16 * 1024 * 1024,
    concurrency: int = 10,
    retries: int = 5,
    transport: httpx.BaseTransport | None = None,
) -> AsyncIterator[bytes]:
    archive = zipstream.ZipStream(compress_type=zipfile.ZIP_STORED, sized=False)
    for source in sources:
        archive.add(
            _sync_file_chunks(
                source,
                segment_size=segment_size,
                concurrency=concurrency,
                retries=retries,
                transport=transport,
            ),
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
