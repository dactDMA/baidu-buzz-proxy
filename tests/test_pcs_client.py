import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from baidu_pcs_client import BaiduPCSClient, BaiduPCSClientError, Credentials
from baidu_pcs_client.errors import english_error_message


def test_credentials_load_active_user_from_pcs_config(tmp_path: Path) -> None:
    config = tmp_path / "pcs_config.json"
    config.write_text(
        json.dumps(
            {
                "baidu_active_uid": 42,
                "baidu_user_list": [
                    {
                        "uid": 42,
                        "bduss": "bduss-value",
                        "stoken": "stoken-value",
                        "cookies": "BAIDUID=id-value; EXTRA=extra-value",
                    }
                ],
                "appid": 266719,
                "pcs_addr": "pcs.baidu.com",
                "pan_ua": "test-pan-agent",
            }
        ),
        encoding="utf-8",
    )

    credentials = Credentials.from_pcs_config(config)

    assert credentials.uid == 42
    assert credentials.cookies == {
        "BAIDUID": "id-value",
        "EXTRA": "extra-value",
        "BDUSS": "bduss-value",
        "STOKEN": "stoken-value",
    }
    assert credentials.pan_user_agent == "test-pan-agent"


@pytest.mark.asyncio
async def test_list_directory_preserves_exact_sizes() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["method"] == "list"
        assert request.url.params["path"] == "/source"
        return httpx.Response(
            200,
            json={
                "list": [
                    {
                        "fs_id": 123,
                        "path": "/source/base.rar",
                        "server_filename": "base.rar",
                        "size": 1530435088,
                        "isdir": 0,
                        "md5": "abcd",
                    }
                ]
            },
        )

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        entries = await client.list_directory("/source")
    finally:
        await client.close()

    assert len(entries) == 1
    assert entries[0].fs_id == "123"
    assert entries[0].size_bytes == 1530435088


@pytest.mark.asyncio
async def test_locate_download_uses_known_uid_without_metadata_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.params["method"] == "locatedownload"
        assert request.url.params["path"] == "/source/base.rar"
        assert request.url.params["rand"]
        return httpx.Response(
            200,
            json={
                "urls": [
                    {"url": "https://d.example/base.rar", "encrypt": 0},
                    {"url": "https://encrypted.example/base.rar", "encrypt": 1},
                ]
            },
        )

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        location = await client.locate("/source/base.rar")
    finally:
        await client.close()

    assert location.urls == ("https://d.example/base.rar",)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_passwordless_share_skips_verification() -> None:
    operations: list[str] = []
    share_state = {
        "bdstoken": "token",
        "share_uk": "100",
        "shareid": "200",
        "loginstate": 1,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        operations.append(request.url.path)
        if request.url.path == "/s/1public":
            return httpx.Response(
                200,
                content=f"<script>locals.mset({json.dumps(share_state)});</script>".encode(),
            )
        if request.url.path == "/share/list":
            return httpx.Response(200, json={"errno": 0, "list": [{"fs_id": 123}]})
        if request.url.path == "/share/transfer":
            body = parse_qs((await request.aread()).decode())
            assert body["path"] == ["/ProxyJobs/job"]
            assert json.loads(body["fsidlist"][0]) == [123]
            return httpx.Response(200, json={"errno": 0, "info": []})
        return httpx.Response(404)

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test; STOKEN=token", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.import_share(
            "https://pan.baidu.com/s/1public", "/ProxyJobs/job", extraction_code=""
        )
    finally:
        await client.close()

    assert "/share/verify" not in operations
    assert operations == ["/s/1public", "/share/list", "/share/transfer"]


@pytest.mark.asyncio
async def test_protected_share_verifies_code_before_import() -> None:
    page_visits = 0
    share_state = {
        "bdstoken": "token",
        "share_uk": "100",
        "shareid": "200",
        "loginstate": 1,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_visits
        if request.url.path == "/s/1protected":
            page_visits += 1
            assert "BDCLND=stale" not in request.headers.get("Cookie", "")
            return httpx.Response(
                200,
                content=f"<script>locals.mset({json.dumps(share_state)});</script>".encode(),
            )
        if request.url.path == "/share/verify":
            body = parse_qs((await request.aread()).decode())
            assert body["pwd"] == ["2ac3"]
            return httpx.Response(
                200,
                json={"errno": 0, "randsk": "verified"},
                headers={"Set-Cookie": "BDCLND=fresh; Domain=.baidu.com; Path=/"},
            )
        if request.url.path == "/share/list":
            cookie = request.headers.get("Cookie", "")
            assert "BDCLND=fresh" in cookie
            assert "BDCLND=stale" not in cookie
            return httpx.Response(200, json={"errno": 0, "list": [{"fs_id": 123}]})
        if request.url.path == "/share/transfer":
            return httpx.Response(200, json={"errno": 0})
        return httpx.Response(404)

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test; STOKEN=token; BDCLND=stale", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.import_share("https://pan.baidu.com/s/1protected?pwd=2ac3", "/ProxyJobs/job")
    finally:
        await client.close()

    assert page_visits == 2


def test_list_share_minus_nine_reports_extraction_code() -> None:
    assert (
        english_error_message("list share", -9, "") == "The share requires a valid extraction code"
    )


@pytest.mark.asyncio
async def test_remote_chinese_error_is_exposed_in_english() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error_code": 31066, "error_msg": "文件或目录不存在"},
        )

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BaiduPCSClientError) as captured:
            await client.list_directory("/missing")
    finally:
        await client.close()

    assert captured.value.code == 31066
    assert str(captured.value) == "list directory: The file or folder does not exist (code 31066)"
    assert str(captured.value).isascii()


@pytest.mark.asyncio
async def test_unknown_non_english_error_is_replaced() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_code": 99999, "error_msg": "未知错误"})

    client = BaiduPCSClient(
        Credentials.from_cookie_header("BDUSS=test", uid=42),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BaiduPCSClientError) as captured:
            await client.quota()
    finally:
        await client.close()

    assert str(captured.value) == "quota: Baidu rejected the request (code 99999)"
    assert str(captured.value).isascii()
