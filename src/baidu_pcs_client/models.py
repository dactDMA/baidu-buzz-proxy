from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Quota:
    total_bytes: int
    used_bytes: int

    @property
    def free_bytes(self) -> int:
        return max(0, self.total_bytes - self.used_bytes)


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    fs_id: str
    path: str
    name: str
    size_bytes: int
    is_dir: bool
    md5: str = ""


@dataclass(frozen=True, slots=True)
class DownloadLocation:
    path: str
    urls: tuple[str, ...]
