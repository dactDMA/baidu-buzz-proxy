import asyncio
import io
import re
import threading
import time
import zipfile

import httpx
import pytest

from baidu_buzz_proxy.services.streams import (
    SourceDownloadError,
    SourceFile,
    build_zip_stream,
    stream_baidu_file,
)


def range_response(request: httpx.Request, content: bytes) -> httpx.Response:
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers["Range"])
    assert match is not None
    start, end = map(int, match.groups())
    return httpx.Response(
        206,
        headers={"Content-Range": f"bytes {start}-{end}/{len(content)}"},
        content=content[start : end + 1],
    )


@pytest.mark.asyncio
async def test_parallel_ranges_are_emitted_in_order() -> None:
    content = bytes(range(64))
    active = 0
    max_active = 0
    requested: list[str] = []
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
            requested.append(request.headers["Range"])
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        await asyncio.sleep(0.03 if start == 0 else 0.01)
        async with lock:
            active -= 1
        return range_response(request, content)

    source = SourceFile("file.bin", len(content), ("https://source.test/file",))
    chunks = [
        chunk
        async for chunk in stream_baidu_file(
            source,
            chunk_size=4,
            segment_size=8,
            concurrency=4,
            retries=0,
            transport=httpx.MockTransport(handler),
        )
    ]

    assert b"".join(chunks) == content
    assert max_active == 4
    assert set(requested) == {f"bytes={start}-{start + 7}" for start in range(0, 64, 8)}


@pytest.mark.asyncio
async def test_parallel_range_rejects_ignored_range() -> None:
    content = b"0123456789abcdef"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    source = SourceFile("file.bin", len(content), ("https://source.test/file",))
    with pytest.raises(SourceDownloadError, match="failed after 1 attempts"):
        _ = b"".join(
            [
                chunk
                async for chunk in stream_baidu_file(
                    source,
                    chunk_size=4,
                    segment_size=8,
                    concurrency=2,
                    retries=0,
                    transport=httpx.MockTransport(handler),
                )
            ]
        )


@pytest.mark.asyncio
async def test_failed_range_retries_with_another_source_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = bytes(range(32))
    attempts: list[str] = []

    async def no_sleep(_: float) -> None:
        pass

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        if "mirror-1" in str(request.url):
            return httpx.Response(503)
        return range_response(request, content)

    monkeypatch.setattr("baidu_buzz_proxy.services.streams.asyncio.sleep", no_sleep)
    source = SourceFile(
        "file.bin",
        len(content),
        ("https://mirror-1.test/file", "https://mirror-2.test/file"),
    )
    downloaded = b"".join(
        [
            chunk
            async for chunk in stream_baidu_file(
                source,
                chunk_size=4,
                segment_size=8,
                concurrency=1,
                retries=1,
                transport=httpx.MockTransport(handler),
            )
        ]
    )

    assert downloaded == content
    assert attempts[:2] == [
        "https://mirror-1.test/file",
        "https://mirror-2.test/file",
    ]


@pytest.mark.asyncio
async def test_zip_stream_uses_ordered_parallel_ranges() -> None:
    content = bytes(range(256)) * (12 * 1024)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return range_response(request, content)

    source = SourceFile("folder/file.bin", len(content), ("https://source.test/file",))
    archive_data = b"".join(
        [
            chunk
            async for chunk in build_zip_stream(
                [source],
                segment_size=1024 * 1024,
                concurrency=3,
                retries=0,
                transport=httpx.MockTransport(handler),
            )
        ]
    )

    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        assert archive.read("folder/file.bin") == content
        assert archive.testzip() is None
    assert max_active == 3
