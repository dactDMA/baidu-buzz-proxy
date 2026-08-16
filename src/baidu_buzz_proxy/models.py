from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobState(StrEnum):
    QUEUED_IMPORT = "queued_import"
    IMPORTING = "importing"
    AWAITING_SELECTION = "awaiting_selection"
    QUEUED_TRANSFER = "queued_transfer"
    TRANSFERRING = "transferring"
    CLEANING = "cleaning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    creator_secret_hash: Mapped[str] = mapped_column(String(255))
    share_url: Mapped[str] = mapped_column(Text)
    extraction_code: Mapped[str] = mapped_column(String(16), default="")
    state: Mapped[str] = mapped_column(String(32), index=True)
    status_message: Mapped[str] = mapped_column(String(255), default="")
    temp_path: Mapped[str] = mapped_column(Text, default="")
    temp_fs_id: Mapped[str] = mapped_column(String(32), default="")
    output_name: Mapped[str] = mapped_column(String(255), default="")
    result_url: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    transferred_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    items: Mapped[list[JobItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobItem.relative_path"
    )


class JobItem(Base):
    __tablename__ = "job_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    fs_id: Mapped[str] = mapped_column(String(32))
    remote_path: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    is_dir: Mapped[bool] = mapped_column(Boolean)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[Job] = relationship(back_populates="items")
