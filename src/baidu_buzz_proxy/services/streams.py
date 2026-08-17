from __future__ import annotations

import asyncio
import re
import time
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Lock
from urllib.parse import urljoin, urlsplit, urlunsplit

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


@dataclass(frozen=True, slots=True)
class _CdnRequest:
    url: str
    logical_url: str
    authority: str | None = None


FileStartCallback = Callable[[int, SourceFile], None]
RouteStatusCallback = Callable[[str], None]


_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.I)
_BAIDU_CDN_FALLBACK_HOSTS = (
    "cdn.baidupcs.com",
    "nd7.baidupcs.com",
    "bjbgp01.baidupcs.com",
    "allall02.baidupcs.com",
    "allall12.baidupcs.com",
)
_CDN_PROBE_TIMEOUT = httpx.Timeout(8, connect=5)
_CDN_PREFERENCE_TTL_SECONDS = 300
_MAX_CDN_REDIRECTS = 5
_cdn_preference_lock = Lock()
_cdn_preference: tuple[str, float] | None = None


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


def _download_headers(start: int, end: int, total_size: int) -> dict[str, str]:
    if start == 0 and end == total_size - 1:
        return {}
    return {"Range": f"bytes={start}-{end}"}


def _download_error_detail(error: Exception | None) -> str:
    if error is None:
        return "unknown download error"
    if isinstance(error, httpx.HTTPStatusError):
        host = urlsplit(str(error.request.url)).hostname or "Baidu CDN"
        return f"HTTP {error.response.status_code} from {host}"
    if isinstance(error, httpx.RequestError):
        host = urlsplit(str(error.request.url)).hostname or "Baidu CDN"
        return f"{type(error).__name__} while contacting {host}"
    return str(error)


def _cdn_request_headers(
    request: _CdnRequest, headers: dict[str, str], cookie_header: str = ""
) -> dict[str, str]:
    result = headers.copy()
    host = (urlsplit(request.url).hostname or "").lower()
    if cookie_header and host.endswith(".baidupcs.com"):
        result["Cookie"] = cookie_header
    if request.authority is not None:
        result["Host"] = request.authority
    return result


def _follow_pinned_https_redirect(request: _CdnRequest, location: str) -> _CdnRequest:
    source = urlsplit(request.url)
    target = urlsplit(urljoin(request.logical_url, location))
    source_host = (source.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if not source_host.endswith(".baidupcs.com") or not target_host.endswith(".baidupcs.com"):
        raise SourceDownloadError("Baidu redirected outside its HTTPS CDN")
    if target.port not in {None, 443}:
        raise SourceDownloadError("Baidu redirected to an unsupported HTTPS CDN port")
    authority = target_host if target.port is None else f"{target_host}:{target.port}"
    return _CdnRequest(
        url=urlunsplit(("https", source_host, target.path, target.query, target.fragment)),
        logical_url=urlunsplit(("https", authority, target.path, target.query, target.fragment)),
        authority=authority,
    )


def _expand_baidu_cdn_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    parsed_urls = [urlsplit(url) for url in urls]
    baidu_urls = [
        parsed
        for parsed in parsed_urls
        if (parsed.hostname or "").lower().endswith(".baidupcs.com")
    ]
    if not baidu_urls:
        return urls
    template = baidu_urls[0]
    hosts = dict.fromkeys(
        [
            *((parsed.hostname or "").lower() for parsed in baidu_urls),
            *_BAIDU_CDN_FALLBACK_HOSTS,
        ]
    )
    return tuple(
        urlunsplit((template.scheme, host, template.path, template.query, template.fragment))
        for host in hosts
        if host
    )


def _preferred_cdn_host() -> str:
    global _cdn_preference
    with _cdn_preference_lock:
        if _cdn_preference is None:
            return ""
        host, expires_at = _cdn_preference
        if expires_at <= time.monotonic():
            _cdn_preference = None
            return ""
        return host


def _remember_cdn_url(url: str) -> None:
    global _cdn_preference
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith(".baidupcs.com"):
        return
    with _cdn_preference_lock:
        _cdn_preference = (host, time.monotonic() + _CDN_PREFERENCE_TTL_SECONDS)


def _forget_cdn_url(url: str) -> None:
    global _cdn_preference
    host = (urlsplit(url).hostname or "").lower()
    with _cdn_preference_lock:
        if _cdn_preference is not None and _cdn_preference[0] == host:
            _cdn_preference = None


def _prefer_cached_cdn(candidates: tuple[str, ...]) -> tuple[str, ...] | None:
    host = _preferred_cdn_host()
    if not host:
        return None
    preferred = [url for url in candidates if (urlsplit(url).hostname or "").lower() == host]
    if not preferred:
        return None
    return tuple(preferred + [url for url in candidates if url not in preferred])


async def _select_async_download_urls(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    status: RouteStatusCallback | None = None,
    cookie_header: str = "",
) -> tuple[str, ...]:
    if not any((urlsplit(url).hostname or "").endswith(".baidupcs.com") for url in urls):
        return urls
    candidates = _expand_baidu_cdn_urls(urls)
    if len(candidates) <= 1:
        return candidates
    cached = _prefer_cached_cdn(candidates)
    if cached is not None:
        if status:
            status(f"Using cached HTTPS Baidu CDN: {urlsplit(cached[0]).hostname}")
        return cached
    if status:
        status(f"Checking {len(candidates)} HTTPS Baidu CDN routes")

    async def probe(index: int, url: str) -> tuple[float, int, str] | None:
        started = time.monotonic()
        try:
            request = _CdnRequest(url=url, logical_url=url)
            for _ in range(_MAX_CDN_REDIRECTS + 1):
                async with client.stream(
                    "GET",
                    request.url,
                    headers=_cdn_request_headers(
                        request, {"Range": "bytes=0-0"}, cookie_header
                    ),
                    timeout=_CDN_PROBE_TIMEOUT,
                ) as response:
                    if response.is_redirect and response.headers.get("Location"):
                        request = _follow_pinned_https_redirect(
                            request, response.headers["Location"]
                        )
                        continue
                    response.raise_for_status()
                    if response.status_code not in {200, 206}:
                        return None
                    return time.monotonic() - started, index, url
            return None
        except (httpx.HTTPError, SourceDownloadError):
            return None

    results = await asyncio.gather(*(probe(index, url) for index, url in enumerate(candidates)))
    available = sorted(result for result in results if result is not None)
    if not available:
        if status:
            status("No HTTPS Baidu CDN passed the probe; retrying each route")
        return candidates
    preferred = [url for _, _, url in available]
    _remember_cdn_url(preferred[0])
    if status:
        status(f"Selected HTTPS Baidu CDN: {urlsplit(preferred[0]).hostname}")
    return tuple(preferred + [url for url in candidates if url not in preferred])


def _select_sync_download_urls(
    client: httpx.Client,
    urls: tuple[str, ...],
    status: RouteStatusCallback | None = None,
    cookie_header: str = "",
) -> tuple[str, ...]:
    if not any((urlsplit(url).hostname or "").endswith(".baidupcs.com") for url in urls):
        return urls
    candidates = _expand_baidu_cdn_urls(urls)
    if len(candidates) <= 1:
        return candidates
    cached = _prefer_cached_cdn(candidates)
    if cached is not None:
        if status:
            status(f"Using cached HTTPS Baidu CDN: {urlsplit(cached[0]).hostname}")
        return cached
    if status:
        status(f"Checking {len(candidates)} HTTPS Baidu CDN routes")

    def probe(index: int, url: str) -> tuple[float, int, str] | None:
        started = time.monotonic()
        try:
            request = _CdnRequest(url=url, logical_url=url)
            for _ in range(_MAX_CDN_REDIRECTS + 1):
                with client.stream(
                    "GET",
                    request.url,
                    headers=_cdn_request_headers(
                        request, {"Range": "bytes=0-0"}, cookie_header
                    ),
                    timeout=_CDN_PROBE_TIMEOUT,
                ) as response:
                    if response.is_redirect and response.headers.get("Location"):
                        request = _follow_pinned_https_redirect(
                            request, response.headers["Location"]
                        )
                        continue
                    response.raise_for_status()
                    if response.status_code not in {200, 206}:
                        return None
                    return time.monotonic() - started, index, url
            return None
        except (httpx.HTTPError, SourceDownloadError):
            return None

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        results = list(executor.map(lambda item: probe(*item), enumerate(candidates)))
    available = sorted(result for result in results if result is not None)
    if not available:
        if status:
            status("No HTTPS Baidu CDN passed the probe; retrying each route")
        return candidates
    preferred = [url for _, _, url in available]
    _remember_cdn_url(preferred[0])
    if status:
        status(f"Selected HTTPS Baidu CDN: {urlsplit(preferred[0]).hostname}")
    return tuple(preferred + [url for url in candidates if url not in preferred])


async def _download_async_segment(
    client: httpx.AsyncClient,
    source: SourceFile,
    index: int,
    segment_size: int,
    retries: int,
    route_status: RouteStatusCallback | None = None,
    cookie_header: str = "",
) -> bytes:
    start, end = _segment_bounds(index, source.size_bytes, segment_size)
    expected_size = end - start + 1
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        url = source.urls[attempt % len(source.urls)]
        if route_status and index == 0:
            route_status(
                f"Connecting to HTTPS Baidu CDN {urlsplit(url).hostname}: "
                f"attempt {attempt + 1} of {retries + 1}"
            )
        try:
            data = bytearray()
            request = _CdnRequest(url=url, logical_url=url)
            for _ in range(_MAX_CDN_REDIRECTS + 1):
                async with client.stream(
                    "GET",
                    request.url,
                    headers=_cdn_request_headers(
                        request,
                        _download_headers(start, end, source.size_bytes),
                        cookie_header,
                    ),
                ) as response:
                    if response.is_redirect and response.headers.get("Location"):
                        request = _follow_pinned_https_redirect(
                            request, response.headers["Location"]
                        )
                        continue
                    _validate_range_response(response, start, end, source.size_bytes)
                    if response.is_stream_consumed:
                        data.extend(response.content)
                    else:
                        async for chunk in response.aiter_raw():
                            data.extend(chunk)
                            if len(data) > expected_size:
                                raise SourceDownloadError("Baidu returned too much range data")
                    break
            else:
                raise SourceDownloadError("Baidu returned too many CDN redirects")
            if len(data) != expected_size:
                raise SourceDownloadError("Baidu returned an incomplete range")
            _remember_cdn_url(url)
            return bytes(data)
        except (httpx.HTTPError, SourceDownloadError) as error:
            _forget_cdn_url(url)
            last_error = error
            if attempt < retries:
                await asyncio.sleep(min(2**attempt, 10))
    raise SourceDownloadError(
        f"Baidu download {start}-{end} failed after {retries + 1} attempts: "
        f"{_download_error_detail(last_error)}"
    ) from last_error


async def stream_baidu_file(
    source: SourceFile,
    *,
    chunk_size: int = 1024 * 1024,
    segment_size: int = 16 * 1024 * 1024,
    concurrency: int = 10,
    retries: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
    on_route_status: RouteStatusCallback | None = None,
    cookie_header: str = "",
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
        follow_redirects=False,
        timeout=httpx.Timeout(60, connect=10, read=120),
        limits=httpx.Limits(
            max_connections=max(concurrency, len(_BAIDU_CDN_FALLBACK_HOSTS) + 4),
            max_keepalive_connections=concurrency,
        ),
        transport=transport,
    ) as client:
        source = replace(
            source,
            urls=await _select_async_download_urls(
                client,
                source.urls,
                status=on_route_status,
                cookie_header=cookie_header,
            ),
        )
        try:
            while next_submit < concurrency:
                tasks[next_submit] = asyncio.create_task(
                    _download_async_segment(
                        client,
                        source,
                        next_submit,
                        segment_size,
                        retries,
                        on_route_status,
                        cookie_header,
                    )
                )
                next_submit += 1
            for next_yield in range(count):
                data = await tasks.pop(next_yield)
                if next_submit < count:
                    tasks[next_submit] = asyncio.create_task(
                        _download_async_segment(
                            client,
                            source,
                            next_submit,
                            segment_size,
                            retries,
                            on_route_status,
                            cookie_header,
                        )
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
    route_status: RouteStatusCallback | None = None,
    cookie_header: str = "",
) -> bytes:
    start, end = _segment_bounds(index, source.size_bytes, segment_size)
    expected_size = end - start + 1
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        url = source.urls[attempt % len(source.urls)]
        if route_status and index == 0:
            route_status(
                f"Connecting to HTTPS Baidu CDN {urlsplit(url).hostname}: "
                f"attempt {attempt + 1} of {retries + 1}"
            )
        try:
            data = bytearray()
            request = _CdnRequest(url=url, logical_url=url)
            for _ in range(_MAX_CDN_REDIRECTS + 1):
                with client.stream(
                    "GET",
                    request.url,
                    headers=_cdn_request_headers(
                        request,
                        _download_headers(start, end, source.size_bytes),
                        cookie_header,
                    ),
                ) as response:
                    if response.is_redirect and response.headers.get("Location"):
                        request = _follow_pinned_https_redirect(
                            request, response.headers["Location"]
                        )
                        continue
                    _validate_range_response(response, start, end, source.size_bytes)
                    if response.is_stream_consumed:
                        data.extend(response.content)
                    else:
                        for chunk in response.iter_raw():
                            data.extend(chunk)
                            if len(data) > expected_size:
                                raise SourceDownloadError("Baidu returned too much range data")
                    break
            else:
                raise SourceDownloadError("Baidu returned too many CDN redirects")
            if len(data) != expected_size:
                raise SourceDownloadError("Baidu returned an incomplete range")
            _remember_cdn_url(url)
            return bytes(data)
        except (httpx.HTTPError, SourceDownloadError) as error:
            _forget_cdn_url(url)
            last_error = error
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
    raise SourceDownloadError(
        f"Baidu download {start}-{end} failed after {retries + 1} attempts: "
        f"{_download_error_detail(last_error)}"
    ) from last_error


def _sync_file_chunks(
    source: SourceFile,
    *,
    chunk_size: int = 1024 * 1024,
    segment_size: int = 16 * 1024 * 1024,
    concurrency: int = 10,
    retries: int = 5,
    transport: httpx.BaseTransport | None = None,
    on_route_status: RouteStatusCallback | None = None,
    cookie_header: str = "",
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
    with httpx.Client(
        headers={"User-Agent": BAIDU_DOWNLOAD_USER_AGENT, "Accept-Encoding": "identity"},
        follow_redirects=False,
        timeout=httpx.Timeout(60, connect=10, read=120),
        limits=httpx.Limits(
            max_connections=max(concurrency, len(_BAIDU_CDN_FALLBACK_HOSTS) + 4),
            max_keepalive_connections=concurrency,
        ),
        transport=transport,
    ) as client:
        source = replace(
            source,
            urls=_select_sync_download_urls(
                client,
                source.urls,
                status=on_route_status,
                cookie_header=cookie_header,
            ),
        )
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            while next_submit < concurrency:
                futures[next_submit] = executor.submit(
                    _download_sync_segment,
                    client,
                    source,
                    next_submit,
                    segment_size,
                    retries,
                    on_route_status,
                    cookie_header,
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
                            on_route_status,
                            cookie_header,
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
    on_file_start: FileStartCallback | None = None,
    on_route_status: RouteStatusCallback | None = None,
    cookie_header: str = "",
) -> AsyncIterator[bytes]:
    archive = zipstream.ZipStream(compress_type=zipfile.ZIP_STORED, sized=False)
    for index, source in enumerate(sources, start=1):
        chunks = _sync_file_chunks(
            source,
            segment_size=segment_size,
            concurrency=concurrency,
            retries=retries,
            transport=transport,
            on_route_status=on_route_status,
            cookie_header=cookie_header,
        )
        if on_file_start:
            chunks = _notify_file_start(chunks, index, source, on_file_start)
        archive.add(
            chunks,
            source.archive_name,
            size=source.size_bytes,
        )
    return async_from_sync(iter(archive))


def _notify_file_start(
    chunks: Iterator[bytes],
    index: int,
    source: SourceFile,
    callback: FileStartCallback,
) -> Iterator[bytes]:
    callback(index, source)
    yield from chunks


async def track_progress(
    stream: AsyncIterator[bytes], callback: Callable[[int], None]
) -> AsyncIterator[bytes]:
    transferred = 0
    async for chunk in stream:
        transferred += len(chunk)
        callback(transferred)
        yield chunk
