from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from baidu_buzz_proxy.services.quota import QuotaSnapshot


class BaiduError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True, slots=True)
class BaiduItem:
    fs_id: str
    remote_path: str
    relative_path: str
    name: str
    is_dir: bool
    size_bytes: int


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL_RE = re.compile(r"https?://[^\s|]+")
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?I?B)\s*$", re.I)
_DATETIME_PATTERN = r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}"
_ALIGNED_LISTING_ROW_RE = re.compile(
    rf"^\s*\d+\s+(?P<fs_id>\d+)\s+\d+\s+"
    rf"(?P<size>-|[0-9]+(?:\.[0-9]+)?\s*[KMGTPE]?I?B)\s+"
    rf"{_DATETIME_PATTERN}\s+{_DATETIME_PATTERN}\s+"
    r"(?:(?P<md5>[0-9a-f]{32})\s+)?(?P<name>.+?)\s*$",
    re.I | re.M,
)
_SIZE_FACTORS = {
    "B": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
    "PB": 1024**5,
    "PIB": 1024**5,
    "EB": 1024**6,
    "EIB": 1024**6,
}


def parse_size(value: str) -> int:
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Unsupported size: {value}")
    number, unit = match.groups()
    return int(float(number) * _SIZE_FACTORS[unit.upper()])


def _table_rows(output: str) -> list[list[str]]:
    rows: list[list[str]] = []
    clean = _ANSI_RE.sub("", output)
    for line in clean.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", "+"} for cell in cells if cell):
            rows.append(cells)
    return rows


def parse_detailed_listing(output: str, directory: str, root: str) -> list[BaiduItem]:
    rows = _table_rows(output)
    header_index = next(
        (index for index, row in enumerate(rows) if "fs_id" in {cell.lower() for cell in row}),
        None,
    )
    result: list[BaiduItem] = []

    if header_index is not None:
        header = [cell.lower() for cell in rows[header_index]]
        fs_index = header.index("fs_id")
        size_index = next(
            (index for index, cell in enumerate(header) if cell in {"文件大小", "size"}), None
        )
        name_index = len(header) - 1
        if size_index is None:
            raise BaiduError("BaiduPCS-Go listing does not contain a size column")
        raw_items = (
            (row[fs_index], row[size_index], row[name_index])
            for row in rows[header_index + 1 :]
            if len(row) == len(header) and row[fs_index].isdigit()
        )
    else:
        clean = _ANSI_RE.sub("", output)
        if not re.search(r"\bFS\s+ID\b", clean, re.I) or "文件(目录)" not in clean:
            raise BaiduError("BaiduPCS-Go returned an unrecognized directory listing")
        raw_items = (
            (match.group("fs_id"), match.group("size"), match.group("name"))
            for match in _ALIGNED_LISTING_ROW_RE.finditer(clean)
        )

    for fs_id, raw_size, raw_name in raw_items:
        is_dir = raw_name.endswith("/") or raw_name.endswith("\\")
        name = raw_name.rstrip("/\\")
        remote_path = str(PurePosixPath(directory) / name)
        relative_path = str(PurePosixPath(remote_path).relative_to(PurePosixPath(root)))
        size = 0 if is_dir or raw_size == "-" else parse_size(raw_size)
        result.append(
            BaiduItem(
                fs_id=fs_id,
                remote_path=remote_path,
                relative_path=relative_path,
                name=name,
                is_dir=is_dir,
                size_bytes=size,
            )
        )
    return result


def parse_metadata(output: str) -> tuple[str, int]:
    clean = _ANSI_RE.sub("", output)
    fs_match = re.search(r"^\s*fs_id\s+(\d+)\s*$", clean, re.I | re.M)
    size_match = re.search(r"^\s*(?:文件大小|size)\s+(\d+)(?:\s*,|\s*$)", clean, re.I | re.M)
    if fs_match:
        return fs_match.group(1), int(size_match.group(1)) if size_match else 0

    fs_id = ""
    size = 0
    for row in _table_rows(clean):
        if len(row) >= 2 and row[0].lower() == "fs_id" and row[1].isdigit():
            fs_id = row[1]
        if len(row) >= 2 and row[0].lower() in {"文件大小", "size"}:
            numeric = row[1].split(",", maxsplit=1)[0].strip()
            if numeric.isdigit():
                size = int(numeric)
    if not fs_id:
        raise BaiduError("Could not read the Baidu fs_id")
    return fs_id, size


class BaiduPCSClient:
    def __init__(self, binary: Path, timeout_seconds: int = 3600) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    async def run(self, *arguments: str, timeout: int | None = None) -> CommandResult:
        if not self.binary.exists():
            raise BaiduError(f"BaiduPCS-Go was not found at {self.binary}")
        process = await asyncio.create_subprocess_exec(
            str(self.binary),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout or self.timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise BaiduError(f"BaiduPCS-Go timed out while running {arguments[0]}") from error

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        result = CommandResult(stdout=stdout, stderr=stderr, returncode=process.returncode or 0)
        if result.returncode != 0:
            message = stderr.strip() or stdout.strip() or "unknown error"
            raise BaiduError(f"BaiduPCS-Go failed: {message}")
        return result

    async def quota(self) -> QuotaSnapshot:
        result = await self.run("quota", timeout=60)
        match = re.search(r"总空间:\s*([^,，]+)[,，]\s*已用空间:\s*([^,，]+)", result.stdout)
        if not match:
            raise BaiduError("Baidu account is not logged in or quota output is invalid")
        try:
            total = parse_size(match.group(1))
            used = parse_size(match.group(2))
        except ValueError as error:
            raise BaiduError("Could not parse Baidu quota") from error
        return QuotaSnapshot(total_bytes=total, used_bytes=used)

    async def mkdir(self, remote_path: str) -> None:
        await self.run("mkdir", remote_path, timeout=120)

    async def change_directory(self, remote_path: str) -> None:
        result = await self.run("cd", remote_path, timeout=60)
        if "失败" in result.stdout or "错误" in result.stdout:
            raise BaiduError(result.stdout.strip())

    async def import_share(self, share_url: str, extraction_code: str) -> None:
        arguments = ["transfer", share_url]
        if extraction_code:
            arguments.append(extraction_code)
        result = await self.run(*arguments)
        if "失败" in result.stdout or "错误" in result.stdout:
            raise BaiduError(result.stdout.strip())

    async def list_directory(self, directory: str, root: str) -> list[BaiduItem]:
        result = await self.run("ls", "-l", directory, timeout=120)
        return parse_detailed_listing(result.stdout, directory, root)

    async def list_tree(self, root: str) -> list[BaiduItem]:
        pending = [root]
        result: list[BaiduItem] = []
        while pending:
            directory = pending.pop()
            children = await self.list_directory(directory, root)
            exact_children: list[BaiduItem] = []
            for child in children:
                if child.is_dir:
                    exact_children.append(child)
                    continue
                fs_id, exact_size = await self.metadata(child.remote_path)
                exact_children.append(
                    BaiduItem(
                        fs_id=fs_id,
                        remote_path=child.remote_path,
                        relative_path=child.relative_path,
                        name=child.name,
                        is_dir=False,
                        size_bytes=exact_size,
                    )
                )
            children = exact_children
            result.extend(children)
            pending.extend(item.remote_path for item in children if item.is_dir)
        return result

    async def metadata_fs_id(self, remote_path: str) -> str:
        fs_id, _ = await self.metadata(remote_path)
        return fs_id

    async def metadata(self, remote_path: str) -> tuple[str, int]:
        result = await self.run("meta", remote_path, timeout=60)
        return parse_metadata(result.stdout)

    async def locate(self, remote_path: str) -> list[str]:
        result = await self.run("locate", remote_path, timeout=120)
        urls = _URL_RE.findall(_ANSI_RE.sub("", result.stdout))
        if not urls:
            raise BaiduError(f"Baidu returned no download link for {remote_path}")
        return urls

    async def remove(self, remote_path: str) -> None:
        result = await self.run("rm", remote_path, timeout=300)
        if "失败" in result.stdout or "错误" in result.stdout:
            raise BaiduError(result.stdout.strip())

    async def permanently_delete(self, fs_id: str) -> None:
        result = await self.run("recycle", "delete", fs_id, timeout=300)
        if "失败" in result.stdout or "错误" in result.stdout:
            raise BaiduError(result.stdout.strip())


BAIDU_DOWNLOAD_USER_AGENT = (
    "netdisk;P2SP;3.0.0.8;netdisk;11.12.3;ANG-AN00;android-android;10.0;"
    "JSbridge4.4.0;jointBridge;1.1.0;"
)
