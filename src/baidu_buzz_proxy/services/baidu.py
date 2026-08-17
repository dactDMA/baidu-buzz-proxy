from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from baidu_buzz_proxy.services.quota import QuotaSnapshot
from baidu_pcs_client import BaiduPCSClient, BaiduPCSClientError, RemoteEntry
from baidu_pcs_client.credentials import DEFAULT_PAN_USER_AGENT

BaiduError = BaiduPCSClientError
BAIDU_DOWNLOAD_USER_AGENT = DEFAULT_PAN_USER_AGENT


@dataclass(frozen=True, slots=True)
class BaiduItem:
    fs_id: str
    remote_path: str
    relative_path: str
    name: str
    is_dir: bool
    size_bytes: int


ScanProgressCallback = Callable[[str, int, int], Awaitable[None]]
LocateProgressCallback = Callable[[int, int, str], Awaitable[None]]


class BaiduClient:
    def __init__(self, client: BaiduPCSClient) -> None:
        self.client = client

    async def close(self) -> None:
        await self.client.close()

    async def quota(self) -> QuotaSnapshot:
        quota = await self.client.quota()
        return QuotaSnapshot(total_bytes=quota.total_bytes, used_bytes=quota.used_bytes)

    async def mkdir(self, remote_path: str) -> str | None:
        entry = await self.client.mkdir(remote_path)
        return entry.fs_id if entry is not None else None

    async def import_share(self, share_url: str, extraction_code: str, destination: str) -> None:
        await self.client.import_share(share_url, destination, extraction_code)

    async def list_directory(self, directory: str, root: str) -> list[BaiduItem]:
        entries = await self.client.list_directory(directory)
        return [self._item(entry, root) for entry in entries]

    async def list_tree(
        self, root: str, progress: ScanProgressCallback | None = None
    ) -> list[BaiduItem]:
        entries = await self.client.list_tree(root, progress=progress)
        return [self._item(entry, root) for entry in entries]

    async def metadata_fs_id(self, remote_path: str) -> str:
        metadata = await self.client.metadata([remote_path])
        if not metadata:
            raise BaiduError("read metadata", f"Baidu returned no metadata for {remote_path}")
        return metadata[0].fs_id

    async def locate(self, remote_path: str) -> list[str]:
        return list((await self.client.locate(remote_path)).urls)

    async def locate_many(
        self,
        remote_paths: list[str],
        *,
        concurrency: int,
        progress: LocateProgressCallback | None = None,
    ) -> list[list[str]]:
        locations = await self.client.locate_many(
            remote_paths, concurrency=concurrency, progress=progress
        )
        return [list(location.urls) for location in locations]

    async def remove(self, remote_path: str) -> None:
        await self.client.remove([remote_path])

    async def permanently_delete(self, fs_id: str) -> None:
        await self.client.permanently_delete([fs_id])

    @staticmethod
    def _item(entry: RemoteEntry, root: str) -> BaiduItem:
        relative = str(PurePosixPath(entry.path).relative_to(PurePosixPath(root)))
        return BaiduItem(
            fs_id=entry.fs_id,
            remote_path=entry.path,
            relative_path=relative,
            name=entry.name,
            is_dir=entry.is_dir,
            size_bytes=entry.size_bytes,
        )
