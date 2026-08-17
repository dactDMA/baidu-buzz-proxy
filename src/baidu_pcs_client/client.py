from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from baidu_pcs_client.credentials import Credentials
from baidu_pcs_client.errors import (
    BaiduPCSAuthenticationError,
    BaiduPCSClientError,
    BaiduPCSNetworkError,
    english_error_message,
)
from baidu_pcs_client.models import DownloadLocation, Quota, RemoteEntry
from baidu_pcs_client.signing import locate_download_signature

PAN_APP_ID = 250528
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SHARE_STATE_RE = re.compile(rb"locals\.mset\((\{.*?\})\);", re.S)

ScanProgress = Callable[[str, int, int], Awaitable[None]]
LocateProgress = Callable[[int, int, str], Awaitable[None]]


class BaiduPCSClient:
    def __init__(
        self,
        credentials: Credentials,
        *,
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self.uid = credentials.uid
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 30))
        self._transport = transport
        self.client = self._new_http_client(credentials.cookies)

    def _new_http_client(self, cookies: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cookies=cookies,
            follow_redirects=True,
            timeout=self._timeout,
            transport=self._transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def quota(self) -> Quota:
        payload = await self._pcs_request("GET", "quota", "info", operation="quota")
        return Quota(total_bytes=int(payload["quota"]), used_bytes=int(payload["used"]))

    async def mkdir(self, path: str) -> RemoteEntry | None:
        try:
            payload = await self._pcs_request(
                "POST", "file", "mkdir", operation="create directory", params={"path": path}
            )
        except BaiduPCSClientError as error:
            if error.code == 31061:
                return None
            raise
        return self._remote_entry(payload) if payload.get("fs_id") else None

    async def list_directory(self, path: str) -> list[RemoteEntry]:
        payload = await self._pcs_request(
            "GET",
            "file",
            "list",
            operation="list directory",
            params={"path": path or "/", "by": "name", "order": "asc"},
        )
        entries = payload.get("list", [])
        if not isinstance(entries, list):
            raise BaiduPCSClientError("list directory", "Baidu returned an invalid file list")
        return [self._remote_entry(item) for item in entries if isinstance(item, dict)]

    async def list_tree(
        self,
        root: str,
        *,
        concurrency: int = 8,
        progress: ScanProgress | None = None,
    ) -> list[RemoteEntry]:
        pending = [root]
        result: list[RemoteEntry] = []
        directories_scanned = 0
        while pending:
            batch_size = min(max(1, concurrency), len(pending))
            batch = [pending.pop() for _ in range(batch_size)]
            if progress:
                for offset, directory in enumerate(batch):
                    await progress(directory, directories_scanned + offset, len(result))
            batches = await asyncio.gather(*(self.list_directory(directory) for directory in batch))
            directories_scanned += len(batch)
            for children in batches:
                result.extend(children)
                pending.extend(item.path for item in children if item.is_dir)
        return result

    async def metadata(self, paths: Iterable[str]) -> list[RemoteEntry]:
        path_list = list(paths)
        if not path_list:
            return []
        param = json.dumps({"list": [{"path": path} for path in path_list]}, separators=(",", ":"))
        payload = await self._pcs_request(
            "POST",
            "file",
            "meta",
            operation="read metadata",
            files={"param": (None, param)},
        )
        entries = payload.get("list", [])
        if not isinstance(entries, list) or len(entries) != len(path_list):
            raise BaiduPCSClientError("read metadata", "Baidu returned incomplete metadata")
        return [self._remote_entry(item) for item in entries if isinstance(item, dict)]

    async def locate(self, path: str) -> DownloadLocation:
        uid = await self._get_uid()
        params: dict[str, Any] = {
            "ant": "1",
            "check_blue": "1",
            "es": "1",
            "esl": "1",
            "app_id": str(PAN_APP_ID),
            "method": "locatedownload",
            "path": path,
            "ver": "4.0",
            "clienttype": "17",
            "channel": "0",
            "apn_id": "1_0",
            "freeisp": "0",
            "queryfree": "0",
            "use": "0",
        }
        params.update(locate_download_signature(self.credentials.cookies["BDUSS"], uid))
        payload = await self._request_json(
            "POST",
            f"https://{self.credentials.pcs_host}/rest/2.0/pcs/file",
            operation="locate download",
            params=params,
            headers={"User-Agent": self.credentials.pan_user_agent},
        )
        raw_urls = payload.get("urls", [])
        urls = tuple(
            str(item["url"])
            for item in raw_urls
            if isinstance(item, dict) and int(item.get("encrypt", 1)) == 0 and item.get("url")
        )
        if not urls:
            raise BaiduPCSClientError("locate download", f"Baidu returned no URL for {path}")
        return DownloadLocation(path=path, urls=urls)

    async def locate_many(
        self,
        paths: list[str],
        *,
        concurrency: int = 8,
        progress: LocateProgress | None = None,
    ) -> list[DownloadLocation]:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        total = len(paths)

        async def locate_one(index: int, path: str) -> DownloadLocation:
            async with semaphore:
                if progress:
                    await progress(index, total, path)
                return await self.locate(path)

        return list(
            await asyncio.gather(
                *(locate_one(index, path) for index, path in enumerate(paths, start=1))
            )
        )

    async def remove(self, paths: Iterable[str]) -> None:
        path_list = list(paths)
        if not path_list:
            return
        param = json.dumps({"list": [{"path": path} for path in path_list]}, separators=(",", ":"))
        await self._pcs_request(
            "POST",
            "file",
            "delete",
            operation="remove files",
            files={"param": (None, param)},
        )

    async def permanently_delete(self, fs_ids: Iterable[str]) -> None:
        ids = [int(value) for value in fs_ids]
        if not ids:
            return
        await self._request_json(
            "POST",
            "https://pan.baidu.com/api/recycle/delete",
            operation="delete recycle entries",
            data={"fidlist": json.dumps(ids, separators=(",", ":"))},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.credentials.pan_user_agent,
            },
            error_key="errno",
        )

    async def import_share(
        self, share_url: str, destination: str, extraction_code: str = ""
    ) -> None:
        feature, query_code = self._share_feature(share_url)
        code = extraction_code.strip() or query_code
        base_cookies = {
            name: value
            for name, value in self.credentials.cookies.items()
            if name.upper() != "BDCLND"
        }
        async with self._new_http_client(base_cookies) as share_client:
            await self._import_share_in_session(share_client, feature, share_url, destination, code)

    async def _import_share_in_session(
        self,
        share_client: httpx.AsyncClient,
        feature: str,
        share_url: str,
        destination: str,
        code: str,
    ) -> None:
        state = await self._access_share_page(feature, first=True, client=share_client)
        if code:
            verify_payload = await self._request_json(
                "POST",
                "https://pan.baidu.com/share/verify",
                operation="verify share",
                params={
                    "shareid": state["shareid"],
                    "time": str(int(time.time() * 1000)),
                    "clienttype": "1",
                    "uk": state["share_uk"],
                },
                data={
                    "pwd": code,
                    "vcode": "null",
                    "vcode_str": "null",
                    "bdstoken": state["bdstoken"],
                },
                headers={"Referer": share_url, "User-Agent": _BROWSER_USER_AGENT},
                error_key="errno",
                client=share_client,
            )
            if not verify_payload.get("randsk"):
                raise BaiduPCSClientError("verify share", "Baidu did not return randsk")
            state = await self._access_share_page(feature, first=False, client=share_client)

        list_payload = await self._request_json(
            "GET",
            "https://pan.baidu.com/share/list",
            operation="list share",
            params={
                "bdstoken": state["bdstoken"],
                "root": "1",
                "web": "5",
                "app_id": str(PAN_APP_ID),
                "shorturl": feature[1:],
                "channel": "chunlei",
            },
            headers={
                "Referer": f"https://pan.baidu.com/s/{feature}",
                "User-Agent": _BROWSER_USER_AGENT,
            },
            error_key="errno",
            client=share_client,
        )
        items = list_payload.get("list", [])
        fs_ids = [
            int(item["fs_id"]) for item in items if isinstance(item, dict) and item.get("fs_id")
        ]
        if not fs_ids:
            raise BaiduPCSClientError("list share", "the share contains no transferable items")

        await self._request_json(
            "POST",
            "https://pan.baidu.com/share/transfer",
            operation="import share",
            params={
                "shareid": state["shareid"],
                "from": state["share_uk"],
                "bdstoken": state["bdstoken"],
                "app_id": str(PAN_APP_ID),
                "channel": "chunlei",
                "clienttype": "0",
                "web": "1",
            },
            data={"fsidlist": json.dumps(fs_ids, separators=(",", ":")), "path": destination},
            headers={
                "Referer": f"https://pan.baidu.com/s/{feature}",
                "User-Agent": _BROWSER_USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            error_key="errno",
            client=share_client,
        )

    async def _get_uid(self) -> int:
        if self.uid:
            return self.uid
        payload = await self._request_json(
            "GET",
            "https://pan.baidu.com/api/user/getinfo",
            operation="read account id",
            params={"need_selfinfo": "1"},
        )
        records = payload.get("records", [])
        if not records or not records[0].get("uk"):
            raise BaiduPCSAuthenticationError("read account id", "Baidu login is invalid")
        self.uid = int(records[0]["uk"])
        return self.uid

    async def _access_share_page(
        self,
        feature: str,
        *,
        first: bool,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        referer = (
            "https://pan.baidu.com/disk/home"
            if first
            else f"https://pan.baidu.com/share/init?surl={feature[1:]}"
        )
        try:
            response = await (client or self.client).get(
                f"https://pan.baidu.com/s/{feature}",
                headers={"Referer": referer, "User-Agent": _BROWSER_USER_AGENT},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise BaiduPCSNetworkError("access share", str(error)) from error
        if b"platform-non-found" in response.content or b"error-404" in response.content:
            raise BaiduPCSClientError("access share", "the share is unavailable")
        match = _SHARE_STATE_RE.search(response.content)
        if not match:
            raise BaiduPCSClientError("access share", "Baidu returned an unknown share page")
        try:
            state: dict[str, Any] = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise BaiduPCSClientError(
                "access share", "Baidu returned invalid page state"
            ) from error
        required = ("bdstoken", "share_uk", "shareid")
        if any(not state.get(key) for key in required):
            raise BaiduPCSAuthenticationError(
                "access share", "the account cookies must include a valid STOKEN"
            )
        return state

    async def _pcs_request(
        self,
        method: str,
        resource: str,
        pcs_method: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request_params = {"app_id": str(self.credentials.app_id), "method": pcs_method}
        if params:
            request_params.update(params)
        return await self._request_json(
            method,
            f"https://{self.credentials.pcs_host}/rest/2.0/pcs/{resource}",
            operation=operation,
            params=request_params,
            **kwargs,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        error_key: str = "error_code",
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await (client or self.client).request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise BaiduPCSNetworkError(operation, str(error)) from error
        try:
            payload: dict[str, Any] = response.json()
        except json.JSONDecodeError as error:
            try:
                response.raise_for_status()
            except httpx.HTTPError as http_error:
                raise BaiduPCSNetworkError(operation, str(http_error)) from http_error
            raise BaiduPCSClientError(operation, "Baidu returned invalid JSON") from error
        code = int(payload.get(error_key) or 0)
        if code != 0:
            remote_message = str(
                payload.get("error_msg") or payload.get("errmsg") or payload.get("show_msg") or ""
            )
            message = english_error_message(operation, code, remote_message)
            error_type = (
                BaiduPCSAuthenticationError if code in {3, -4, -6, -11} else BaiduPCSClientError
            )
            raise error_type(operation, message, code=code)
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise BaiduPCSNetworkError(operation, str(error)) from error
        return payload

    @staticmethod
    def _remote_entry(payload: dict[str, Any]) -> RemoteEntry:
        path = str(payload.get("path") or "")
        return RemoteEntry(
            fs_id=str(payload.get("fs_id") or ""),
            path=path,
            name=str(payload.get("server_filename") or PurePosixPath(path).name),
            size_bytes=int(payload.get("size") or 0),
            is_dir=bool(int(payload.get("isdir") or 0)),
            md5=str(payload.get("md5") or ""),
        )

    @staticmethod
    def _share_feature(share_url: str) -> tuple[str, str]:
        parsed = urlsplit(share_url)
        query = parse_qs(parsed.query)
        feature = PurePosixPath(parsed.path.rstrip("/")).name
        if feature == "init":
            feature = "1" + query.get("surl", [""])[0]
        if not feature.startswith("1") or len(feature) > 64:
            raise BaiduPCSClientError("parse share", "the share URL is invalid")
        return feature, query.get("pwd", [""])[0]
