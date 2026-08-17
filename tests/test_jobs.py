import asyncio
from pathlib import Path
from typing import Any

import pytest

from baidu_buzz_proxy.config import Settings
from baidu_buzz_proxy.database import Database
from baidu_buzz_proxy.models import Job, JobItem, JobState
from baidu_buzz_proxy.services.baidu import BaiduItem
from baidu_buzz_proxy.services.jobs import JobService, safe_output_name
from baidu_buzz_proxy.services.quota import QuotaSnapshot


class DummyClient:
    pass


class FakeBaidu:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.deleted: list[str] = []

    async def quota(self) -> QuotaSnapshot:
        return QuotaSnapshot(total_bytes=5 * 1024**4, used_bytes=1024**4)

    async def mkdir(self, remote_path: str) -> None:
        pass

    async def change_directory(self, remote_path: str) -> None:
        pass

    async def import_share(self, share_url: str, extraction_code: str) -> None:
        pass

    async def list_tree(self, root: str) -> list[BaiduItem]:
        return [
            BaiduItem(
                fs_id="file-1",
                remote_path=f"{root}/base.rar",
                relative_path="base.rar",
                name="base.rar",
                is_dir=False,
                size_bytes=100,
            )
        ]

    async def metadata_fs_id(self, remote_path: str) -> str:
        return "folder-1"

    async def locate(self, remote_path: str) -> list[str]:
        return ["https://source.test/base.rar"]

    async def remove(self, remote_path: str) -> None:
        self.removed.append(remote_path)

    async def permanently_delete(self, fs_id: str) -> None:
        self.deleted.append(fs_id)


class FakeBuzz:
    async def upload(self, name: str, stream: Any, **options: Any) -> str:
        await options["progress"](100)
        return "https://buzz.test/base"

    async def close(self) -> None:
        pass


def test_safe_output_name_keeps_unicode_and_removes_path_characters() -> None:
    assert safe_output_name("资料/base?#.zip", "fallback.zip") == "资料_base?_.zip"


def test_safe_output_name_sanitizes_the_automatic_fallback() -> None:
    assert safe_output_name("", "Lovely#remake.zip") == "Lovely_remake.zip"


@pytest.mark.asyncio
async def test_selecting_directory_selects_descendant_files(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await database.initialize()
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    service = JobService(database, settings, DummyClient(), DummyClient())  # type: ignore[arg-type]
    job, creator_key = await service.create_job("https://pan.baidu.com/s/test", "abcd")

    async with database.sessions() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        stored.state = JobState.AWAITING_SELECTION
        new_items = [
            JobItem(
                job_id=stored.id,
                fs_id="1",
                remote_path=f"{stored.temp_path}/folder",
                relative_path="folder",
                name="folder",
                is_dir=True,
                size_bytes=0,
            ),
            JobItem(
                job_id=stored.id,
                fs_id="2",
                remote_path=f"{stored.temp_path}/folder/file.bin",
                relative_path="folder/file.bin",
                name="file.bin",
                is_dir=False,
                size_bytes=123,
            ),
            JobItem(
                job_id=stored.id,
                fs_id="3",
                remote_path=f"{stored.temp_path}/other.bin",
                relative_path="other.bin",
                name="other.bin",
                is_dir=False,
                size_bytes=456,
            ),
        ]
        session.add_all(new_items)
        await session.commit()
        directory_id = new_items[0].id

    selected = await service.select_items(
        job.public_id,
        creator_key,
        [directory_id],
        select_all=False,
        output_name="folder",
    )

    assert selected.state == JobState.QUEUED_TRANSFER
    assert selected.output_name == "folder.zip"
    assert selected.total_bytes == 123
    assert [item.relative_path for item in selected.items if item.selected] == [
        "folder",
        "folder/file.bin",
    ]
    await database.close()


@pytest.mark.asyncio
async def test_job_lifecycle_imports_transfers_and_cleans(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    await database.initialize()
    settings = Settings(max_active_jobs=1)
    baidu = FakeBaidu()
    service = JobService(database, settings, baidu, FakeBuzz())  # type: ignore[arg-type]
    await service.start()
    try:
        created, creator_key = await service.create_job("https://pan.baidu.com/s/test", "abcd")
        imported = await _wait_for_state(service, created.public_id, JobState.AWAITING_SELECTION)
        assert len(imported.items) == 1

        await service.select_items(
            created.public_id,
            creator_key,
            [imported.items[0].id],
            select_all=False,
            output_name="",
        )
        completed = await _wait_for_state(service, created.public_id, JobState.COMPLETED)

        assert completed.result_url == "https://buzz.test/base"
        assert completed.cleanup_completed is True
        assert baidu.removed == [created.temp_path]
        assert baidu.deleted == ["folder-1"]
    finally:
        await service.stop()
        await database.close()


async def _wait_for_state(service: JobService, public_id: str, state: JobState) -> Job:
    for _ in range(100):
        job = await service.get_job(public_id)
        if job.state == state:
            return job
        if job.state == JobState.FAILED:
            raise AssertionError(job.error_message)
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job did not reach {state}")
