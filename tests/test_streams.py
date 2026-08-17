import asyncio
import io
import re
import threading
import time
import zipfile

import httpx
import pytest

from baidu_buzz_proxy.services import streams as streams_module
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
async def test_single_segment_uses_plain_get() -> None:
    content = b"small file"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "Range" not in request.headers
        assert "Cookie" not in request.headers
        return httpx.Response(200, content=content)

    source = SourceFile("small.bin", len(content), ("https://source.test/file",))
    downloaded = b"".join(
        [
            chunk
            async for chunk in stream_baidu_file(
                source,
                chunk_size=4,
                segment_size=16,
                concurrency=1,
                retries=0,
                transport=httpx.MockTransport(handler),
                cookie_header="BDUSS=test; STOKEN=token",
            )
        ]
    )

    assert downloaded == content


@pytest.mark.asyncio
async def test_download_failure_exposes_safe_http_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    source = SourceFile(
        "small.bin",
        4,
        ("https://source.test/file?secret=signed-value",),
    )
    with pytest.raises(SourceDownloadError) as raised:
        _ = b"".join(
            [
                chunk
                async for chunk in stream_baidu_file(
                    source,
                    chunk_size=4,
                    segment_size=16,
                    concurrency=1,
                    retries=0,
                    transport=httpx.MockTransport(handler),
                )
            ]
        )

    message = str(raised.value)
    assert "HTTP 403 from source.test" in message
    assert "signed-value" not in message


@pytest.mark.asyncio
async def test_baidu_download_selects_an_available_cdn_domain() -> None:
    content = b"available through fallback"
    probes: set[str] = set()
    downloads: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.headers.get("Range") == "bytes=0-0":
            probes.add(host)
            if host == "cdn.baidupcs.com":
                return httpx.Response(206, content=content[:1])
            raise httpx.ConnectTimeout("unreachable CDN", request=request)
        downloads.append(host)
        if host == "cdn.baidupcs.com":
            return httpx.Response(200, content=content)
        raise httpx.ConnectTimeout("unreachable CDN", request=request)

    source = SourceFile(
        "small.bin",
        len(content),
        ("https://blocked.baidupcs.com/file/test?signature=secret",),
    )
    downloaded = b"".join(
        [
            chunk
            async for chunk in stream_baidu_file(
                source,
                chunk_size=4,
                segment_size=64,
                concurrency=1,
                retries=0,
                transport=httpx.MockTransport(handler),
                cookie_header="BDUSS=test; STOKEN=token",
            )
        ]
    )

    assert downloaded == content
    assert "blocked.baidupcs.com" in probes
    assert "cdn.baidupcs.com" in probes
    assert downloads == ["cdn.baidupcs.com"]


def test_zip_download_selects_an_available_cdn_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        probes.add(host)
        if host == "cdn.baidupcs.com":
            return httpx.Response(206, content=b"x")
        raise httpx.ConnectTimeout("unreachable CDN", request=request)

    monkeypatch.setattr(streams_module, "_cdn_preference", None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        selected = streams_module._select_sync_download_urls(
            client,
            ("https://blocked.baidupcs.com/file/test?signature=secret",),
        )

    assert "blocked.baidupcs.com" in probes
    assert "cdn.baidupcs.com" in probes
    assert httpx.URL(selected[0]).host == "cdn.baidupcs.com"


@pytest.mark.asyncio
async def test_baidu_redirect_stays_on_the_selected_https_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"sticky HTTPS redirect"
    redirected_hosts: list[str] = []
    redirected_authorities: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host != "nd7.baidupcs.com":
            raise httpx.ConnectTimeout("unreachable CDN", request=request)
        assert request.headers["Cookie"] == "BDUSS=test; STOKEN=token"
        if request.url.path == "/file/original":
            return httpx.Response(
                302,
                headers={"Location": "https://bjbgp01.baidupcs.com/file/redirected?token=fresh"},
            )
        redirected_hosts.append(host)
        redirected_authorities.append(request.headers["Host"])
        if request.headers["Host"] != "bjbgp01.baidupcs.com":
            return httpx.Response(403)
        if request.headers.get("Range") == "bytes=0-0":
            return httpx.Response(206, content=content[:1])
        return httpx.Response(200, content=content)

    monkeypatch.setattr(streams_module, "_cdn_preference", None)
    source = SourceFile(
        "small.bin",
        len(content),
        ("https://nd7.baidupcs.com/file/original?token=old",),
    )
    downloaded = b"".join(
        [
            chunk
            async for chunk in stream_baidu_file(
                source,
                chunk_size=4,
                segment_size=64,
                concurrency=1,
                retries=0,
                transport=httpx.MockTransport(handler),
                cookie_header="BDUSS=test; STOKEN=token",
            )
        ]
    )

    assert downloaded == content
    assert redirected_hosts
    assert set(redirected_hosts) == {"nd7.baidupcs.com"}
    assert set(redirected_authorities) == {"bjbgp01.baidupcs.com"}


@pytest.mark.asyncio
async def test_zip_redirect_stays_on_the_selected_https_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"sticky HTTPS ZIP redirect"
    redirected_hosts: list[str] = []
    redirected_authorities: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host != "nd7.baidupcs.com":
            raise httpx.ConnectTimeout("unreachable CDN", request=request)
        assert request.headers["Cookie"] == "BDUSS=test; STOKEN=token"
        if request.url.path == "/file/original":
            return httpx.Response(
                302,
                headers={"Location": "https://allall02.baidupcs.com/file/redirected"},
            )
        redirected_hosts.append(host)
        redirected_authorities.append(request.headers["Host"])
        if request.headers["Host"] != "allall02.baidupcs.com":
            return httpx.Response(403)
        if request.headers.get("Range"):
            return range_response(request, content)
        return httpx.Response(200, content=content)

    monkeypatch.setattr(streams_module, "_cdn_preference", None)
    source = SourceFile(
        "folder/small.bin",
        len(content),
        ("https://nd7.baidupcs.com/file/original?token=old",),
    )
    archive_data = b"".join(
        [
            chunk
            async for chunk in build_zip_stream(
                [source],
                segment_size=64,
                concurrency=1,
                retries=0,
                transport=httpx.MockTransport(handler),
                cookie_header="BDUSS=test; STOKEN=token",
            )
        ]
    )

    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        assert archive.read("folder/small.bin") == content
    assert redirected_hosts
    assert set(redirected_hosts) == {"nd7.baidupcs.com"}
    assert set(redirected_authorities) == {"allall02.baidupcs.com"}


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
    started: list[tuple[int, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
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
                on_file_start=lambda index, item: started.append((index, item.archive_name)),
            )
        ]
    )

    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        assert archive.read("folder/file.bin") == content
        assert archive.testzip() is None
    assert max_active == 3
    assert started == [(1, "folder/file.bin")]
