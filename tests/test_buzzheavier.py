from collections.abc import AsyncIterator

import httpx
import pytest

from baidu_buzz_proxy.services.buzzheavier import BuzzMultipartClient


@pytest.mark.asyncio
async def test_multipart_upload_splits_and_completes() -> None:
    uploaded_parts: list[tuple[int, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        if request.method == "POST" and request.url.path == "/api/upload":
            return httpx.Response(
                200,
                json={"data": {"id": "upload-1", "location": "/parts/upload-1"}},
            )
        if request.method == "PATCH" and request.url.path == "/parts/upload-1":
            uploaded_parts.append(
                (int(request.headers["Upload-Part-Number"]), await request.aread())
            )
            return httpx.Response(204)
        if request.method == "POST" and request.url.path == "/api/upload/upload-1":
            return httpx.Response(200, json={"data": {"url": "https://buzz.test/final"}})
        return httpx.Response(404)

    async def content() -> AsyncIterator[bytes]:
        yield b"abcdefgh"
        yield b"ijklmnop"

    progress: list[int] = []
    preparation: list[int] = []

    async def record_progress(value: int) -> None:
        progress.append(value)

    async def record_preparation(value: int) -> None:
        preparation.append(value)

    client = BuzzMultipartClient(
        "https://buzz.test",
        "",
        part_size=5,
        concurrency=2,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.upload(
            "sample.bin",
            content(),
            progress=record_progress,
            prepare_progress=record_preparation,
            is_cancelled=_not_cancelled,
        )
    finally:
        await client.close()

    assert result == "https://buzz.test/final"
    assert sorted(uploaded_parts) == [
        (1, b"abcde"),
        (2, b"fghij"),
        (3, b"klmno"),
        (4, b"p"),
    ]
    assert progress[-1] == 16
    assert preparation == [5]


@pytest.mark.asyncio
async def test_multipart_upload_reports_progress_within_a_part() -> None:
    content_size = 3 * 1024 * 1024

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/upload":
            return httpx.Response(
                200,
                json={"data": {"id": "upload-2", "location": "/parts/upload-2"}},
            )
        if request.method == "PATCH":
            await request.aread()
            return httpx.Response(204)
        return httpx.Response(200, json={"data": {"url": "https://buzz.test/final"}})

    async def content() -> AsyncIterator[bytes]:
        yield b"x" * content_size

    progress: list[int] = []

    async def record_progress(value: int) -> None:
        progress.append(value)

    client = BuzzMultipartClient(
        "https://buzz.test",
        "",
        part_size=content_size,
        concurrency=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.upload(
            "sample.bin",
            content(),
            progress=record_progress,
            is_cancelled=_not_cancelled,
        )
    finally:
        await client.close()

    assert progress[0] < content_size
    assert progress[-1] == content_size
    assert progress == sorted(progress)


async def _not_cancelled() -> bool:
    return False
